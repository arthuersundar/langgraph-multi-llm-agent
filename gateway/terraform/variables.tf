variable "region" {
  type    = string
  default = "us-east-1"
}

variable "name_prefix" {
  description = "Prefix for every resource name"
  type        = string
  default     = "llm-gateway"
}

variable "default_backend" {
  description = "Backend used when a request's model field does not name one: bedrock or vllm"
  type        = string
  default     = "bedrock"
  validation {
    condition     = contains(["bedrock", "vllm"], var.default_backend)
    error_message = "default_backend must be bedrock or vllm."
  }
}

variable "bedrock_model_id" {
  description = "Model (or cross-region inference profile) used for the bedrock backend"
  type        = string
  default     = "us.amazon.nova-lite-v1:0"
}

variable "bedrock_model_arns" {
  description = "Extra IAM resource ARNs the Lambda may invoke. Empty = Nova models and Nova inference profiles in this account."
  type        = list(string)
  default     = []
}

variable "vllm_base_url" {
  description = "Public OpenAI-compatible URL of a vLLM server, e.g. https://host:8000/v1. Empty = vllm backend disabled."
  type        = string
  default     = ""
}

variable "vllm_model" {
  type    = string
  default = "llama3.2:3b"
}

variable "vllm_api_key" {
  description = "Bearer token the vLLM server expects, if any. Stored in the Lambda environment, so use a throwaway key."
  type        = string
  default     = ""
  sensitive   = true
}

variable "max_tokens_cap" {
  description = "Hard upper limit on max_tokens for any request"
  type        = number
  default     = 1024
}

variable "teams" {
  description = "One API key and usage plan per team"
  type = map(object({
    rate_limit  = number # steady requests per second
    burst_limit = number # short burst allowance
    daily_quota = number # requests per day
  }))
  default = {
    "ml-platform" = { rate_limit = 5, burst_limit = 10, daily_quota = 2000 }
    "data-eng"    = { rate_limit = 2, burst_limit = 5, daily_quota = 500 }
  }
}

variable "hourly_token_alarm" {
  description = "Alarm when one team's input tokens on bedrock exceed this in an hour"
  type        = number
  default     = 200000
}

variable "log_retention_days" {
  type    = number
  default = 14
}

# ---- observability --------------------------------------------------------------------
variable "model_prices" {
  description = "USD per 1M tokens as [input, output], by Bedrock model id, used to estimate cost. These defaults are EXAMPLES: check them against the current Amazon Bedrock price list before trusting any dollar figure."
  type        = map(list(number))
  default = {
    "us.amazon.nova-micro-v1:0" = [0.035, 0.14]
    "us.amazon.nova-lite-v1:0"  = [0.06, 0.24]
    "us.amazon.nova-pro-v1:0"   = [0.80, 3.20]
  }
}

variable "alarm_email" {
  description = "Email address for alarm notifications (empty = alarms only show in the console). AWS sends a confirmation link you must click."
  type        = string
  default     = ""
}

variable "daily_spend_alarm_usd" {
  description = "Alarm when one team's estimated model spend exceeds this in 24 hours"
  type        = number
  default     = 25
}

variable "total_daily_spend_alarm_usd" {
  description = "Alarm when estimated model spend across all teams exceeds this in 24 hours"
  type        = number
  default     = 50
}

variable "error_rate_alarm_pct" {
  description = "Alarm when this percent of requests get a 5xx over two 5-minute periods"
  type        = number
  default     = 5
}

variable "latency_p95_alarm_ms" {
  description = "Alarm when p95 latency at API Gateway exceeds this many milliseconds"
  type        = number
  default     = 15000
}

variable "throttle_alarm_count" {
  description = "Alarm when Bedrock throttles at least this many of one team's requests in 5 minutes"
  type        = number
  default     = 10
}

variable "enable_tracing" {
  description = "AWS X-Ray tracing for API Gateway and the Lambda"
  type        = bool
  default     = true
}

variable "bedrock_max_attempts" {
  description = "Total tries the Lambda makes per Bedrock call (1 = no retry). Each retry of a throttled call adds seconds of waiting; callers can retry on their own."
  type        = number
  default     = 2
  validation {
    condition     = var.bedrock_max_attempts >= 1 && var.bedrock_max_attempts <= 5
    error_message = "bedrock_max_attempts must be between 1 and 5."
  }
}
