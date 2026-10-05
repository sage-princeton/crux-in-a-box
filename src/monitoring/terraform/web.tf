variable "web_enabled" {
  type    = bool
  default = false
}
variable "web_image_digest" {
  type    = string
  default = ""
  validation {
    condition     = var.web_image_digest == "" || can(regex("^sha256:[a-f0-9]{64}$", var.web_image_digest))
    error_message = "Supply an immutable web image digest."
  }
}
variable "proxy_image_digest" {
  type    = string
  default = ""
  validation {
    condition     = var.proxy_image_digest == "" || can(regex("^sha256:[a-f0-9]{64}$", var.proxy_image_digest))
    error_message = "Supply an immutable scanned proxy image digest."
  }
}
variable "incident_state_enabled" {
  type        = bool
  default     = false
  description = "Enable worker ingestion and switch the digest after the historical migration."
}

resource "aws_dynamodb_table" "incidents" {
  name         = "${var.name}-incidents"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  range_key    = "sk"
  attribute {
    name = "pk"
    type = "S"
  }
  attribute {
    name = "sk"
    type = "S"
  }
  attribute {
    name = "instance_id"
    type = "S"
  }
  attribute {
    name = "id"
    type = "S"
  }
  global_secondary_index {
    name            = "instance-incidents"
    projection_type = "ALL"
    key_schema {
      attribute_name = "instance_id"
      key_type       = "HASH"
    }
    key_schema {
      attribute_name = "id"
      key_type       = "RANGE"
    }
  }
  attribute {
    name = "status"
    type = "S"
  }
  attribute {
    name = "updated_at"
    type = "N"
  }
  global_secondary_index {
    name            = "status-updated"
    projection_type = "ALL"
    key_schema {
      attribute_name = "status"
      key_type       = "HASH"
    }
    key_schema {
      attribute_name = "updated_at"
      key_type       = "RANGE"
    }
  }
  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }
  point_in_time_recovery { enabled = true }
  server_side_encryption {
    enabled     = true
    kms_key_arn = aws_kms_key.state.arn
  }
  deletion_protection_enabled = true
  depends_on                  = [aws_iam_role_policy.state_encryption]
}

resource "terraform_data" "incident_activation" {
  lifecycle {
    precondition {
      condition     = !var.incident_state_enabled || var.web_enabled
      error_message = "The stateful digest requires the authenticated web app."
    }
  }
}

resource "aws_s3_object" "legacy_incident_link" {
  count         = var.incident_state_enabled ? 1 : 0
  bucket        = aws_s3_bucket.evidence.id
  key           = "reviews/incidents/index.html"
  content_type  = "text/html; charset=utf-8"
  cache_control = "no-store"
  content       = "<!doctype html><html lang=\"en\"><meta charset=\"utf-8\"><meta http-equiv=\"refresh\" content=\"0;url=${local.web_origin}\"><title>CRUX incident log</title><p><a href=\"${local.web_origin}\">Open the incident log (AWS login required)</a></p></html>"
}

locals {
  web_service_configuration = var.web_enabled ? templatefile("${path.module}/web-service.sh.tftpl", {
    region      = var.region,
    repository  = aws_ecr_repository.monitoring.repository_url,
    image       = var.web_image_digest,
    proxy_image = var.proxy_image_digest,
    table       = aws_dynamodb_table.incidents.name,
    origin      = local.web_origin,
    revision    = var.revision
  }) : ""
  web_origin = var.web_enabled ? "https://${replace(aws_eip.web[0].public_ip, ".", "-")}.sslip.io" : ""
}

resource "aws_eip" "web" {
  count  = var.web_enabled ? 1 : 0
  domain = "vpc"
  tags   = { Name = "crux-incident-web" }
}
resource "aws_eip_association" "web" {
  count         = var.web_enabled ? 1 : 0
  instance_id   = aws_instance.web[0].id
  allocation_id = aws_eip.web[0].id
}
resource "aws_security_group" "web" {
  count       = var.web_enabled ? 1 : 0
  name        = "crux-incident-web"
  description = "Public incident website; administration through SSM only"
  vpc_id      = local.vpc_id
}
resource "aws_vpc_security_group_ingress_rule" "web" {
  description       = "Public HTTPS website and HTTP redirect/ACME validation"
  for_each          = var.web_enabled ? toset(["80", "443"]) : toset([])
  security_group_id = aws_security_group.web[0].id
  ip_protocol       = "tcp"
  from_port         = tonumber(each.key)
  to_port           = tonumber(each.key)
  cidr_ipv4         = "0.0.0.0/0"
}
resource "aws_vpc_security_group_egress_rule" "web" {
  description       = "AWS APIs, image pulls and ACME certificate renewal over HTTPS"
  count             = var.web_enabled ? 1 : 0
  security_group_id = aws_security_group.web[0].id
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = "0.0.0.0/0"
}
resource "aws_iam_role" "web" {
  count = var.web_enabled ? 1 : 0
  name  = "crux-incident-web"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Principal = { Service = "ec2.amazonaws.com" }, Action = "sts:AssumeRole"
  }] })
}
resource "aws_iam_instance_profile" "web" {
  count = var.web_enabled ? 1 : 0
  name  = "crux-incident-web"
  role  = aws_iam_role.web[0].name
}
resource "aws_iam_role_policy_attachment" "web_ssm" {
  count      = var.web_enabled ? 1 : 0
  role       = aws_iam_role.web[0].name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}
resource "aws_iam_role_policy" "web" {
  count = var.web_enabled ? 1 : 0
  role  = aws_iam_role.web[0].id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["ecr:GetAuthorizationToken"], Resource = "*" },
    { Effect = "Allow", Action = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability"], Resource = aws_ecr_repository.monitoring.arn },
    { Effect = "Allow", Action = ["ssm:GetParameter"], Resource = "arn:aws:ssm:${var.region}:${var.account_id}:parameter/crux/monitoring/web" },
    { Effect = "Allow", Action = ["dynamodb:GetItem", "dynamodb:Query"], Resource = [aws_dynamodb_table.incidents.arn, "${aws_dynamodb_table.incidents.arn}/index/instance-incidents"] },
    { Effect    = "Allow", Action = ["dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem"], Resource = aws_dynamodb_table.incidents.arn,
      Condition = { "ForAllValues:StringLike" = { "dynamodb:LeadingKeys" = ["INCIDENTS", "HISTORY#*", "LOGIN#*", "SESSION#*"] } }
    }
  ] })
}
resource "aws_iam_role_policy" "incident_ingestion" {
  role = aws_iam_role.job["review"].id
  policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect    = "Allow", Action = ["dynamodb:GetItem", "dynamodb:Query", "dynamodb:PutItem", "dynamodb:UpdateItem"], Resource = aws_dynamodb_table.incidents.arn,
    Condition = { "ForAllValues:StringLike" = { "dynamodb:LeadingKeys" = ["INCIDENTS", "HISTORY#*", "REVIEW#*", "FLEET"] } }
  }] })
}
data "aws_ami" "web" {
  count       = var.web_enabled ? 1 : 0
  most_recent = true
  owners      = ["amazon"]
  filter {
    name   = "name"
    values = ["al2023-ami-2023.*-kernel-6.1-x86_64"]
  }
  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}
resource "aws_instance" "web" {
  count                       = var.web_enabled ? 1 : 0
  ami                         = data.aws_ami.web[0].id
  instance_type               = "t3.small"
  subnet_id                   = local.subnet_ids[0]
  vpc_security_group_ids      = [aws_security_group.web[0].id]
  iam_instance_profile        = aws_iam_instance_profile.web[0].name
  associate_public_ip_address = true
  user_data_replace_on_change = false
  metadata_options {
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }
  root_block_device {
    encrypted   = true
    volume_type = "gp3"
    volume_size = 16
  }
  user_data = templatefile("${path.module}/web-user-data.sh.tftpl", {
    service_configuration = local.web_service_configuration
  })
  tags = { Name = "crux-incident-web", CruxRole = "monitoring-web" }
  lifecycle {
    precondition {
      condition     = var.web_image_digest != "" && var.proxy_image_digest != ""
      error_message = "Build, scan and push the web and proxy images before enabling the web EC2."
    }
    ignore_changes = [ami]
  }
  depends_on = [aws_iam_role_policy.web, aws_iam_role_policy_attachment.web_ssm]
}
output "incident_web_url" { value = local.web_origin }
output "incident_table" { value = aws_dynamodb_table.incidents.name }
output "incident_web_instance" { value = try(aws_instance.web[0].id, null) }

output "incident_web_configuration" { value = local.web_service_configuration }
