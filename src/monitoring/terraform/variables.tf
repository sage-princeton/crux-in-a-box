variable "region" {
  type    = string
  default = "us-east-1"
}
variable "account_id" {
  type = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.account_id))
    error_message = "Supply the verified deployment account ID."
  }
}
variable "name" {
  type    = string
  default = "crux-monitoring"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,35}$", var.name))
    error_message = "Use a short lowercase resource prefix."
  }
}
variable "image_digest" {
  type    = string
  default = ""
  validation {
    condition     = var.image_digest == "" || can(regex("^sha256:[a-f0-9]{64}$", var.image_digest))
    error_message = "Use the pushed ECR image digest, never a mutable tag."
  }
}
variable "revision" {
  type    = string
  default = "unbuilt"
}
variable "registry_file" {
  type        = string
  description = "Local non-secret registry JSON. Fleet discovery is always enabled and expires_at is always set to zero."
}
variable "secrets_parameter_arn" {
  type        = string
  description = "Existing SSM SecureString containing only MONITORING_* credentials; no value enters Terraform state."
  validation {
    condition     = can(regex("^arn:aws:ssm:[a-z0-9-]+:[0-9]{12}:parameter/crux/monitoring/", var.secrets_parameter_arn))
    error_message = "Use a dedicated /crux/monitoring/ parameter; do not grant access to shared workload credentials."
  }
}
variable "secrets_kms_key_arn" {
  type    = string
  default = ""
}
variable "evidence_log_group_arns" {
  type    = list(string)
  default = []
}
variable "inspection_cidrs" {
  type        = list(string)
  default     = []
  description = "Existing reachable target export addresses (/32s recommended); only outbound TCP 22 is opened."
}
variable "vpc_id" {
  type        = string
  default     = ""
  description = "Optional existing VPC for private SFTP reachability. Only new monitoring resources are created."
}
variable "subnet_ids" {
  type        = list(string)
  default     = []
  description = "When using an existing VPC, supply subnets with established outbound HTTPS connectivity."
}
variable "public_incident_log" {
  type        = bool
  default     = false
  description = "Deprecated compatibility input; ignored. All S3 objects remain private."
}
