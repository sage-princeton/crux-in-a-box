# Separate keys keep application data permissions independent of log delivery.
resource "aws_kms_key" "state" {
  description             = "${var.name} DynamoDB state and incident history"
  enable_key_rotation     = true
  deletion_window_in_days = 30
  policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Sid       = "AccountAdministration", Effect = "Allow",
    Principal = { AWS = "arn:aws:iam::${var.account_id}:root" },
    Action    = "kms:*", Resource = "*"
  }] })
  lifecycle { prevent_destroy = true }
}

resource "aws_iam_role_policy" "state_encryption" {
  for_each = merge({ for name, role in aws_iam_role.job : name => role.name },
  var.web_enabled ? { web = aws_iam_role.web[0].name } : {})
  name = "monitoring-state-encryption"
  role = each.value
  policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect   = "Allow",
    Action   = ["kms:Encrypt", "kms:Decrypt", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:DescribeKey"],
    Resource = aws_kms_key.state.arn,
    Condition = { StringEquals = {
      "kms:ViaService"    = "dynamodb.${var.region}.amazonaws.com",
      "kms:CallerAccount" = var.account_id
    } }
  }] })
}

resource "aws_kms_key" "logs" {
  description             = "${var.name} job and VPC flow logs"
  enable_key_rotation     = true
  deletion_window_in_days = 30
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Sid       = "AccountAdministration", Effect = "Allow",
      Principal = { AWS = "arn:aws:iam::${var.account_id}:root" },
    Action = "kms:*", Resource = "*" },
    { Sid       = "CloudWatchEncryption", Effect = "Allow",
      Principal = { Service = "logs.${var.region}.amazonaws.com" },
      Action    = ["kms:Encrypt", "kms:Decrypt", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:DescribeKey"], Resource = "*",
      Condition = { ArnLike = {
        "kms:EncryptionContext:aws:logs:arn" = "arn:aws:logs:${var.region}:${var.account_id}:log-group:/crux/monitoring/${var.name}*"
      } }
    }
  ] })
  lifecycle { prevent_destroy = true }
}

resource "aws_default_security_group" "monitoring" {
  count  = local.own_vpc ? 1 : 0
  vpc_id = aws_vpc.monitoring[0].id
  # No rules: only the explicit worker/web security groups may carry traffic.
}

resource "aws_cloudwatch_log_group" "flow" {
  count             = local.own_vpc ? 1 : 0
  name              = "/crux/monitoring/${var.name}/vpc-flow"
  retention_in_days = 365
  kms_key_id        = aws_kms_key.logs.arn
}

resource "aws_iam_role" "flow" {
  count = local.own_vpc ? 1 : 0
  name  = "${var.name}-flow-logs"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Principal = { Service = "vpc-flow-logs.amazonaws.com" }, Action = "sts:AssumeRole",
    Condition = { StringEquals = { "aws:SourceAccount" = var.account_id },
    ArnLike = { "aws:SourceArn" = "arn:aws:ec2:${var.region}:${var.account_id}:vpc-flow-log/*" } }
  }] })
}

resource "aws_iam_role_policy" "flow" {
  count = local.own_vpc ? 1 : 0
  role  = aws_iam_role.flow[0].id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"],
    Resource = "${aws_cloudwatch_log_group.flow[0].arn}:*" },
    { Effect = "Allow", Action = "logs:DescribeLogGroups", Resource = "*" }
  ] })
}

resource "aws_flow_log" "monitoring" {
  count                    = local.own_vpc ? 1 : 0
  vpc_id                   = aws_vpc.monitoring[0].id
  traffic_type             = "ALL"
  iam_role_arn             = aws_iam_role.flow[0].arn
  log_destination          = aws_cloudwatch_log_group.flow[0].arn
  max_aggregation_interval = 60
  depends_on               = [aws_iam_role_policy.flow]
}

resource "aws_s3_bucket" "access_logs" {
  #checkov:skip=CKV_AWS_18:Terminal access-log destination; logging this bucket to itself would generate a recursive log stream.
  #checkov:skip=CKV_AWS_144:Single-region access logs have bounded retention; versioning is enabled.
  #checkov:skip=CKV_AWS_145:S3 server access-log delivery requires SSE-S3 on its destination bucket.
  #checkov:skip=CKV2_AWS_62:Audit logs are inspected directly; there is no asynchronous notification consumer.
  bucket        = "${var.name}-${var.account_id}-${var.region}-access"
  force_destroy = false
}

resource "aws_s3_bucket_public_access_block" "access_logs" {
  bucket                  = aws_s3_bucket.access_logs.id
  block_public_acls       = true
  ignore_public_acls      = true
  block_public_policy     = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "access_logs" {
  bucket = aws_s3_bucket.access_logs.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "access_logs" {
  bucket = aws_s3_bucket.access_logs.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "access_logs" {
  bucket = aws_s3_bucket.access_logs.id
  rule {
    id     = "audit-retention"
    status = "Enabled"
    filter {}
    expiration { days = 365 }
    noncurrent_version_expiration { noncurrent_days = 365 }
    abort_incomplete_multipart_upload { days_after_initiation = 7 }
  }
}

resource "aws_s3_bucket_policy" "access_logs" {
  bucket = aws_s3_bucket.access_logs.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect   = "Deny", Principal = "*", Action = "s3:*",
      Resource = [aws_s3_bucket.access_logs.arn, "${aws_s3_bucket.access_logs.arn}/*"],
    Condition = { Bool = { "aws:SecureTransport" = "false" } } },
    { Effect   = "Allow", Principal = { Service = "logging.s3.amazonaws.com" }, Action = "s3:PutObject",
      Resource = "${aws_s3_bucket.access_logs.arn}/evidence/*",
      Condition = { StringEquals = { "aws:SourceAccount" = var.account_id },
      ArnEquals = { "aws:SourceArn" = aws_s3_bucket.evidence.arn } }
    }
  ] })
  depends_on = [aws_s3_bucket_public_access_block.access_logs]
}

resource "aws_s3_bucket_logging" "evidence" {
  bucket        = aws_s3_bucket.evidence.id
  target_bucket = aws_s3_bucket.access_logs.id
  target_prefix = "evidence/"
  depends_on    = [aws_s3_bucket_policy.access_logs]
}
