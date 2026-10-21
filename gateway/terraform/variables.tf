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
