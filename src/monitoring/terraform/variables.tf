variable "region" {
  type = string
}
variable "account_id" { type = string }
variable "name" { type = string }
variable "vpc_id" { type = string }
variable "web_subnet_id" { type = string }
variable "web_image_digest" {
  type = string
  validation {
    condition     = can(regex("^sha256:[a-f0-9]{64}$", var.web_image_digest))
    error_message = "Use an immutable app image digest."
  }
}
variable "proxy_image_digest" {
  type = string
  validation {
    condition     = can(regex("^sha256:[a-f0-9]{64}$", var.proxy_image_digest))
    error_message = "Use an immutable proxy image digest."
  }
}
variable "revision" {
  type = string
}
