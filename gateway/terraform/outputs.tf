output "base_url" {
  description = "Use as the OpenAI-compatible base URL (the client appends /chat/completions)"
  value       = "${aws_api_gateway_stage.prod.invoke_url}/v1"
}

output "api_keys" {
  description = "Per-team API keys. Read one with: terraform output -json api_keys"
  value       = { for team, key in aws_api_gateway_api_key.team : team => key.value }
  sensitive   = true
}

output "dashboard_url" {
  description = "CloudWatch dashboard for the gateway"
  value       = "https://${var.region}.console.aws.amazon.com/cloudwatch/home?region=${var.region}#dashboards:name=${var.name_prefix}"
}

output "alarm_topic_arn" {
  value = aws_sns_topic.alarms.arn
}
