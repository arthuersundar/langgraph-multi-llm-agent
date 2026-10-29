data "aws_caller_identity" "me" {}

locals {
  bedrock_arns = length(var.bedrock_model_arns) > 0 ? var.bedrock_model_arns : [
    "arn:aws:bedrock:*::foundation-model/amazon.nova-*",
    "arn:aws:bedrock:*:${data.aws_caller_identity.me.account_id}:inference-profile/*.amazon.nova-*",
  ]
}

# ---------------------------------------------------------------------------------------
# Lambda
# ---------------------------------------------------------------------------------------
data "archive_file" "lambda" {
  type        = "zip"
  source_file = "${path.module}/../lambda/handler.py"
  output_path = "${path.module}/build/handler.zip"
}

resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aws/lambda/${var.name_prefix}"
  retention_in_days = var.log_retention_days
}

resource "aws_iam_role" "lambda" {
  name = "${var.name_prefix}-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy" "lambda" {
  name = "${var.name_prefix}-policy"
  role = aws_iam_role.lambda.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "Logs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.lambda.arn}:*"
      },
      {
        Sid      = "Tracing"
        Effect   = "Allow"
        Action   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"]
        Resource = "*"
      },
      {
        Sid      = "InvokeModels"
        Effect   = "Allow"
        Action   = ["bedrock:InvokeModel"]
        Resource = local.bedrock_arns
      },
    ]
  })
}

resource "aws_lambda_function" "gateway" {
  function_name    = var.name_prefix
  role             = aws_iam_role.lambda.arn
  runtime          = "python3.12"
  handler          = "handler.handler"
  filename         = data.archive_file.lambda.output_path
  source_code_hash = data.archive_file.lambda.output_base64sha256
  timeout          = 28 # API Gateway gives up at 29 seconds
  memory_size      = 256

  tracing_config {
    mode = var.enable_tracing ? "Active" : "PassThrough"
  }

  environment {
    variables = {
      DEFAULT_BACKEND  = var.default_backend
      BEDROCK_MODEL_ID = var.bedrock_model_id
      VLLM_BASE_URL    = var.vllm_base_url
      VLLM_MODEL       = var.vllm_model
      VLLM_API_KEY     = var.vllm_api_key
      MAX_TOKENS_CAP   = tostring(var.max_tokens_cap)
      MODEL_PRICES     = jsonencode(var.model_prices)

      BEDROCK_MAX_ATTEMPTS = tostring(var.bedrock_max_attempts)
      KEY_TEAMS        = jsonencode({ for team, key in aws_api_gateway_api_key.team : key.id => team })
    }
  }

  depends_on = [aws_cloudwatch_log_group.lambda, aws_iam_role_policy.lambda]
}

resource "aws_lambda_permission" "apigw" {
  statement_id  = "AllowApiGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.gateway.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.this.execution_arn}/*/POST/v1/chat/completions"
}

# ---------------------------------------------------------------------------------------
# API Gateway (REST API, because usage plans and API keys need it)
# POST /v1/chat/completions
# ---------------------------------------------------------------------------------------
resource "aws_api_gateway_rest_api" "this" {
  name = var.name_prefix
  endpoint_configuration {
    types = ["REGIONAL"]
  }
}

resource "aws_api_gateway_resource" "v1" {
  rest_api_id = aws_api_gateway_rest_api.this.id
  parent_id   = aws_api_gateway_rest_api.this.root_resource_id
  path_part   = "v1"
}

resource "aws_api_gateway_resource" "chat" {
  rest_api_id = aws_api_gateway_rest_api.this.id
  parent_id   = aws_api_gateway_resource.v1.id
  path_part   = "chat"
}

resource "aws_api_gateway_resource" "completions" {
  rest_api_id = aws_api_gateway_rest_api.this.id
  parent_id   = aws_api_gateway_resource.chat.id
  path_part   = "completions"
}

resource "aws_api_gateway_method" "post" {
  rest_api_id      = aws_api_gateway_rest_api.this.id
  resource_id      = aws_api_gateway_resource.completions.id
  http_method      = "POST"
  authorization    = "NONE"
  api_key_required = true # no key, no access
}

resource "aws_api_gateway_integration" "lambda" {
  rest_api_id             = aws_api_gateway_rest_api.this.id
  resource_id             = aws_api_gateway_resource.completions.id
  http_method             = aws_api_gateway_method.post.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.gateway.invoke_arn
}

resource "aws_api_gateway_deployment" "this" {
  rest_api_id = aws_api_gateway_rest_api.this.id
  triggers = {
    redeploy = sha1(jsonencode([
      aws_api_gateway_resource.completions.id,
      aws_api_gateway_method.post.id,
      aws_api_gateway_method.post.api_key_required,
      aws_api_gateway_integration.lambda.id,
    ]))
  }
  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_api_gateway_stage" "prod" {
  rest_api_id   = aws_api_gateway_rest_api.this.id
  deployment_id = aws_api_gateway_deployment.this.id
  stage_name    = "prod"

  xray_tracing_enabled = var.enable_tracing
}

# Safety net for the whole API, on top of each team's own usage plan.
resource "aws_api_gateway_method_settings" "all" {
  rest_api_id = aws_api_gateway_rest_api.this.id
  stage_name  = aws_api_gateway_stage.prod.stage_name
  method_path = "*/*"
  settings {
    throttling_rate_limit  = 20
    throttling_burst_limit = 40
  }
}

# ---------------------------------------------------------------------------------------
# One API key and usage plan per team
# ---------------------------------------------------------------------------------------
resource "aws_api_gateway_api_key" "team" {
  for_each = var.teams
  name     = "${var.name_prefix}-${each.key}"
  enabled  = true
}

resource "aws_api_gateway_usage_plan" "team" {
  for_each = var.teams
  name     = "${var.name_prefix}-${each.key}"

  api_stages {
    api_id = aws_api_gateway_rest_api.this.id
    stage  = aws_api_gateway_stage.prod.stage_name
  }
  throttle_settings {
    rate_limit  = each.value.rate_limit
    burst_limit = each.value.burst_limit
  }
  quota_settings {
    limit  = each.value.daily_quota
    period = "DAY"
  }
}

resource "aws_api_gateway_usage_plan_key" "team" {
  for_each      = var.teams
  key_id        = aws_api_gateway_api_key.team[each.key].id
  key_type      = "API_KEY"
  usage_plan_id = aws_api_gateway_usage_plan.team[each.key].id
}

# ---------------------------------------------------------------------------------------
# Basic alarms (more in observability.tf). They notify the SNS topic defined there.
# ---------------------------------------------------------------------------------------
resource "aws_cloudwatch_metric_alarm" "lambda_errors" {
  alarm_name          = "${var.name_prefix}-lambda-errors"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = aws_lambda_function.gateway.function_name }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 5
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
}

resource "aws_cloudwatch_metric_alarm" "team_tokens" {
  for_each            = var.teams
  alarm_name          = "${var.name_prefix}-${each.key}-bedrock-tokens-per-hour"
  namespace           = "LLMGateway"
  metric_name         = "InputTokens"
  dimensions          = { Team = each.key, Backend = "bedrock" }
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 1
  threshold           = var.hourly_token_alarm
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
}
