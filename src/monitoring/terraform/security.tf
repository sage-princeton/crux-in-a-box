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
