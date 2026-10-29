# ---------------------------------------------------------------------------------------
# Observability: SNS topic, alarms, CloudWatch dashboard.
# Data sources: the gateway Lambda's embedded-metric line (namespace LLMGateway, dimensions
# Team + Backend), API Gateway's own metrics (ApiName + Stage), Lambda's metrics, and the
# Lambda log group (Logs Insights). Tracing (X-Ray) is switched on in main.tf.
# ---------------------------------------------------------------------------------------
locals {
  backends      = ["bedrock", "vllm"]
  alarm_actions = [aws_sns_topic.alarms.arn]
  api_dims      = { ApiName = aws_api_gateway_rest_api.this.name, Stage = aws_api_gateway_stage.prod.stage_name }
  # one series per (team, backend), used by dashboard widgets and spend alarms
  team_backend = flatten([for t in keys(var.teams) : [for b in local.backends : { team = t, backend = b, id = "c_${replace(t, "-", "_")}_${b}" }]])
}

resource "aws_sns_topic" "alarms" {
  name = "${var.name_prefix}-alarms"
}

# Email alerts: set alarm_email, apply, then click the confirmation link AWS emails you.
resource "aws_sns_topic_subscription" "email" {
  count     = var.alarm_email == "" ? 0 : 1
  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

# ---- alarms ---------------------------------------------------------------------------
# More than error_rate_alarm_pct % of requests failed with a 5xx (ignored below 5 requests per 5 min).
resource "aws_cloudwatch_metric_alarm" "api_error_rate" {
  alarm_name          = "${var.name_prefix}-5xx-rate"
  alarm_description   = "Share of requests answered with a server error (backend failures included)"
  comparison_operator = "GreaterThanThreshold"
  threshold           = var.error_rate_alarm_pct
  evaluation_periods  = 2
  datapoints_to_alarm = 2
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions

  metric_query {
    id          = "rate"
    expression  = "IF(req < 5, 0, 100 * err / req)"
    label       = "5xx %"
    return_data = true
  }
  metric_query {
    id = "err"
    metric {
      namespace   = "AWS/ApiGateway"
      metric_name = "5XXError"
      period      = 300
      stat        = "Sum"
      dimensions  = local.api_dims
    }
  }
  metric_query {
    id = "req"
    metric {
      namespace   = "AWS/ApiGateway"
      metric_name = "Count"
      period      = 300
      stat        = "Sum"
      dimensions  = local.api_dims
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "api_latency_p95" {
  alarm_name          = "${var.name_prefix}-latency-p95"
  alarm_description   = "95th percentile end-to-end latency at API Gateway"
  namespace           = "AWS/ApiGateway"
  metric_name         = "Latency"
  dimensions          = local.api_dims
  extended_statistic  = "p95"
  period              = 300
  evaluation_periods  = 3
  datapoints_to_alarm = 2
  threshold           = var.latency_p95_alarm_ms
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions
}

# The Bedrock backend is throttling a team's requests (the 429s the Lambda passes on).
resource "aws_cloudwatch_metric_alarm" "backend_throttles" {
  for_each            = var.teams
  alarm_name          = "${var.name_prefix}-${each.key}-backend-throttles"
  alarm_description   = "Bedrock throttled this team's requests"
  namespace           = "LLMGateway"
  metric_name         = "BackendThrottles"
  dimensions          = { Team = each.key, Backend = "bedrock" }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = var.throttle_alarm_count
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
}

# Estimated spend per team over the alarm's 24 h window (CostUsd = tokens x model_prices).
resource "aws_cloudwatch_metric_alarm" "team_daily_spend" {
  for_each            = var.teams
  alarm_name          = "${var.name_prefix}-${each.key}-daily-spend"
  alarm_description   = "Estimated model spend for ${each.key} above ${var.daily_spend_alarm_usd} USD in 24 hours"
  comparison_operator = "GreaterThanThreshold"
  threshold           = var.daily_spend_alarm_usd
  evaluation_periods  = 1
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions

  metric_query {
    id          = "total"
    expression  = "SUM(METRICS())"
    label       = "USD per day"
    return_data = true
  }
  dynamic "metric_query" {
    for_each = [for tb in local.team_backend : tb if tb.team == each.key]
    content {
      id = metric_query.value.id
      metric {
        namespace   = "LLMGateway"
        metric_name = "CostUsd"
        period      = 86400
        stat        = "Sum"
        dimensions  = { Team = metric_query.value.team, Backend = metric_query.value.backend }
      }
    }
  }
}

# Same idea across all teams: the account-level stop-the-bleeding alarm.
resource "aws_cloudwatch_metric_alarm" "total_daily_spend" {
  alarm_name          = "${var.name_prefix}-total-daily-spend"
  alarm_description   = "Estimated model spend across all teams above ${var.total_daily_spend_alarm_usd} USD in 24 hours"
  comparison_operator = "GreaterThanThreshold"
  threshold           = var.total_daily_spend_alarm_usd
  evaluation_periods  = 1
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions

  metric_query {
    id          = "total"
    expression  = "SUM(METRICS())"
    label       = "USD per day, all teams"
    return_data = true
  }
  dynamic "metric_query" {
    for_each = local.team_backend
    content {
      id = metric_query.value.id
      metric {
        namespace   = "LLMGateway"
        metric_name = "CostUsd"
        period      = 86400
        stat        = "Sum"
        dimensions  = { Team = metric_query.value.team, Backend = metric_query.value.backend }
      }
    }
  }
}

# ---- dashboard ------------------------------------------------------------------------
locals {
  log_group = aws_cloudwatch_log_group.lambda.name
  gw        = "LLMGateway"

  # [namespace, metric, dim, value, dim, value, options]; Sum per series, labelled "team / backend"
  series = { for m in ["Requests", "InputTokens", "OutputTokens", "ServerErrors", "BackendThrottles"] :
    m => [for tb in local.team_backend : [local.gw, m, "Team", tb.team, "Backend", tb.backend, { stat = "Sum", label = "${tb.team} / ${tb.backend}" }]]
  }
  cost_series = [for t in keys(var.teams) : [local.gw, "CostUsd", "Team", t, "Backend", "bedrock", { stat = "Sum", label = t }]]

  widgets = [
    { type = "text", x = 0, y = 0, width = 24, height = 2, properties = { markdown = "## LLM gateway\nRequests, tokens and *estimated* cost per team (cost = tokens x `model_prices`, Bedrock only; self-hosted vLLM shows 0). Alarms notify the SNS topic `${var.name_prefix}-alarms`." } },
    { type = "metric", x = 0, y = 2, width = 8, height = 6, properties = { title = "Requests per team / backend", region = var.region, view = "timeSeries", stacked = true, period = 300, metrics = local.series["Requests"] } },
    { type = "metric", x = 8, y = 2, width = 8, height = 6, properties = { title = "Estimated Bedrock cost, USD per 5 min", region = var.region, view = "timeSeries", stacked = true, period = 300, metrics = local.cost_series } },
    { type = "metric", x = 16, y = 2, width = 8, height = 6, properties = { title = "Estimated Bedrock cost, whole time range (USD)", region = var.region, view = "bar", setPeriodToTimeRange = true, stat = "Sum", metrics = local.cost_series } },
    { type = "metric", x = 0, y = 8, width = 8, height = 6, properties = { title = "Input tokens", region = var.region, view = "timeSeries", stacked = true, period = 300, metrics = local.series["InputTokens"] } },
    { type = "metric", x = 8, y = 8, width = 8, height = 6, properties = { title = "Output tokens", region = var.region, view = "timeSeries", stacked = true, period = 300, metrics = local.series["OutputTokens"] } },
    { type = "metric", x = 16, y = 8, width = 8, height = 6, properties = { title = "Server errors and backend throttles", region = var.region, view = "timeSeries", period = 300, metrics = concat(local.series["ServerErrors"], local.series["BackendThrottles"]) } },
    { type = "metric", x = 0, y = 14, width = 12, height = 6, properties = {
      title = "Latency at API Gateway (ms): total vs. time in the model backend", region = var.region, view = "timeSeries", period = 300,
      metrics = [
        ["AWS/ApiGateway", "Latency", "ApiName", aws_api_gateway_rest_api.this.name, "Stage", aws_api_gateway_stage.prod.stage_name, { stat = "p50", label = "total p50" }],
        ["...", { stat = "p95", label = "total p95" }],
        ["...", { stat = "p99", label = "total p99" }],
        ["AWS/ApiGateway", "IntegrationLatency", "ApiName", aws_api_gateway_rest_api.this.name, "Stage", aws_api_gateway_stage.prod.stage_name, { stat = "p95", label = "Lambda p95" }],
    ] } },
    { type = "metric", x = 12, y = 14, width = 12, height = 6, properties = {
      title = "API Gateway responses", region = var.region, view = "timeSeries", period = 300, stat = "Sum",
      metrics = [
        ["AWS/ApiGateway", "Count", "ApiName", aws_api_gateway_rest_api.this.name, "Stage", aws_api_gateway_stage.prod.stage_name, { label = "requests" }],
        ["AWS/ApiGateway", "4XXError", "ApiName", aws_api_gateway_rest_api.this.name, "Stage", aws_api_gateway_stage.prod.stage_name, { label = "4xx (bad key, quota, bad request)" }],
        ["AWS/ApiGateway", "5XXError", "ApiName", aws_api_gateway_rest_api.this.name, "Stage", aws_api_gateway_stage.prod.stage_name, { label = "5xx" }],
    ] } },
    { type = "alarm", x = 0, y = 20, width = 24, height = 3, properties = { title = "Alarms", alarms = concat(
      [aws_cloudwatch_metric_alarm.api_error_rate.arn, aws_cloudwatch_metric_alarm.api_latency_p95.arn,
      aws_cloudwatch_metric_alarm.lambda_errors.arn, aws_cloudwatch_metric_alarm.total_daily_spend.arn],
      [for a in aws_cloudwatch_metric_alarm.team_daily_spend : a.arn],
      [for a in aws_cloudwatch_metric_alarm.backend_throttles : a.arn],
    ) } },
    { type = "log", x = 0, y = 23, width = 12, height = 7, properties = {
      title = "Spend and latency by team (Logs Insights, selected time range)", region = var.region, view = "table",
      query = "SOURCE '${local.log_group}' | filter ispresent(Team) | stats count(*) as requests, sum(InputTokens) as inTokens, sum(OutputTokens) as outTokens, sum(CostUsd) as costUsd, avg(BackendMs) as avgBackendMs, pct(LatencyMs, 95) as p95Ms by Team, Backend | sort costUsd desc"
    } },
    { type = "log", x = 12, y = 23, width = 12, height = 7, properties = {
      title = "Recent failed requests (Logs Insights)", region = var.region, view = "table",
      query = "SOURCE '${local.log_group}' | filter ispresent(Team) and Status >= 400 | fields @timestamp, Team, Backend, Model, Status, LatencyMs, RequestId | sort @timestamp desc | limit 20"
    } },
  ]
}

resource "aws_cloudwatch_dashboard" "gateway" {
  dashboard_name = var.name_prefix
  dashboard_body = jsonencode({ widgets = local.widgets })
}
