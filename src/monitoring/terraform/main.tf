locals {
  web_origin = "https://${replace(aws_eip.web[0].public_ip, ".", "-")}.sslip.io"
}

resource "aws_s3_bucket" "evidence" {
  #checkov:skip=CKV_AWS_144:Single-region monitoring snapshots have bounded retention and versioning.
  #checkov:skip=CKV2_AWS_62:The polling worker consumes evidence directly.
  #checkov:skip=CKV_AWS_145:Private TLS-only snapshot storage uses SSE-S3; no customer key is required.
  bucket = "${var.name}-${var.account_id}-${var.region}"
}
resource "aws_s3_bucket_public_access_block" "evidence" {
  bucket                  = aws_s3_bucket.evidence.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
resource "aws_s3_bucket_versioning" "evidence" {
  bucket = aws_s3_bucket.evidence.id
  versioning_configuration { status = "Enabled" }
}
resource "aws_s3_bucket_server_side_encryption_configuration" "evidence" {
  bucket = aws_s3_bucket.evidence.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}
resource "aws_s3_bucket_policy" "tls" {
  bucket = aws_s3_bucket.evidence.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect    = "Deny", Principal = "*", Action = "s3:*",
    Resource  = [aws_s3_bucket.evidence.arn, "${aws_s3_bucket.evidence.arn}/*"],
    Condition = { Bool = { "aws:SecureTransport" = "false" } }
  }] })
  depends_on = [aws_s3_bucket_public_access_block.evidence]
}
resource "aws_s3_bucket_lifecycle_configuration" "evidence" {
  bucket = aws_s3_bucket.evidence.id
  rule {
    id     = "monitoring-retention"
    status = "Enabled"
    filter {}
    expiration { days = 90 }
    noncurrent_version_expiration { noncurrent_days = 90 }
    abort_incomplete_multipart_upload { days_after_initiation = 7 }
  }
}
resource "aws_ecr_repository" "monitoring" {
  #checkov:skip=CKV_AWS_136:ECR's AES256 encryption avoids replacing the existing immutable release repository.
  name                 = var.name
  image_tag_mutability = "IMMUTABLE"
  image_scanning_configuration { scan_on_push = true }
}

data "aws_subnets" "database" {
  filter {
    name   = "vpc-id"
    values = [var.vpc_id]
  }
  filter {
    name   = "default-for-az"
    values = ["true"]
  }
}
resource "aws_db_subnet_group" "monitoring" {
  name       = "${var.name}-postgres"
  subnet_ids = data.aws_subnets.database.ids
  lifecycle {
    precondition {
      condition     = length(data.aws_subnets.database.ids) >= 2
      error_message = "RDS requires existing subnets in at least two availability zones."
    }
  }
}
resource "aws_security_group" "database" {
  name        = "${var.name}-postgres"
  description = "PostgreSQL reachable only from the monitoring host"
  vpc_id      = var.vpc_id
}
resource "aws_vpc_security_group_ingress_rule" "postgres" {
  security_group_id            = aws_security_group.database.id
  referenced_security_group_id = aws_security_group.web[0].id
  description                  = "Monitoring app and Alembic migrations"
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
}
resource "aws_vpc_security_group_egress_rule" "postgres" {
  security_group_id            = aws_security_group.web[0].id
  referenced_security_group_id = aws_security_group.database.id
  description                  = "Private PostgreSQL"
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
}
resource "aws_db_parameter_group" "monitoring" {
  name   = "${var.name}-postgres18"
  family = "postgres18"
  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }
}
resource "aws_db_instance" "monitoring" {
  #checkov:skip=CKV_AWS_353:This small monitor uses standard RDS metrics and PostgreSQL logs instead of Performance Insights.
  #checkov:skip=CKV_AWS_157:Single-AZ is deliberate for this small monitor; automated backups provide recovery.
  #checkov:skip=CKV_AWS_118:This micro instance uses CloudWatch's standard metrics rather than enhanced monitoring.
  #checkov:skip=CKV_AWS_161:Managed master credentials remain exclusively in Secrets Manager.
  identifier                      = "${var.name}-postgres"
  engine                          = "postgres"
  engine_version                  = "18"
  instance_class                  = "db.t4g.micro"
  allocated_storage               = 20
  max_allocated_storage           = 100
  storage_type                    = "gp3"
  storage_encrypted               = true
  db_name                         = "monitoring"
  username                        = "crux_monitor"
  manage_master_user_password     = true
  db_subnet_group_name            = aws_db_subnet_group.monitoring.name
  vpc_security_group_ids          = [aws_security_group.database.id]
  parameter_group_name            = aws_db_parameter_group.monitoring.name
  publicly_accessible             = false
  backup_retention_period         = 7
  copy_tags_to_snapshot           = true
  deletion_protection             = true
  auto_minor_version_upgrade      = true
  enabled_cloudwatch_logs_exports = ["postgresql", "upgrade"]
  performance_insights_enabled    = false
  skip_final_snapshot             = false
  final_snapshot_identifier       = "${var.name}-postgres-final"
}

resource "aws_ssm_document" "workspace" {
  name            = "${var.name}-copy-workspace"
  document_type   = "Command"
  document_format = "JSON"
  content = jsonencode({
    schemaVersion = "2.2"
    description   = "Stream the whole workspace using temporary credentials scoped to one S3 object"
    parameters = {
      UploadKey = {
        type           = "String", interpolationType = "ENV_VAR"
        allowedPattern = "^workspaces/i-[a-f0-9]+/[0-9]+/workspace.tar.gz$"
      }
      Credentials = { type = "String", interpolationType = "ENV_VAR" }
    }
    mainSteps = [{
      action = "aws:runShellScript", name = "CopyWholeWorkspace"
      inputs = {
        timeoutSeconds = "3600"
        runCommand = [
          "export CRUX_ARCHIVE_BUCKET='${aws_s3_bucket.evidence.id}' CRUX_ARCHIVE_REGION='${var.region}'",
          "python3 - <<'CRUX_PYTHON'\n${file("${path.module}/../workspace_copy.py")}\nCRUX_PYTHON"
        ]
      }
    }]
  })
}
resource "aws_iam_role_policy_attachment" "run_ssm" {
  role       = "crux-system-role"
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}
resource "aws_iam_role" "workspace_upload" {
  name                 = "${var.name}-workspace-upload"
  max_session_duration = 3600
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Principal = { AWS = aws_iam_role.web[0].arn }, Action = "sts:AssumeRole"
  }] })
}
resource "aws_iam_role_policy" "workspace_upload" {
  role = aws_iam_role.workspace_upload.name
  policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect   = "Allow", Action = ["s3:PutObject", "s3:AbortMultipartUpload"],
    Resource = "${aws_s3_bucket.evidence.arn}/workspaces/*"
  }] })
}

resource "aws_iam_role_policy" "plan" {
  name   = "monitoring-plan"
  role   = "crux-monitoring-plan"
  policy = file("${path.module}/../ci/plan-policy.json")
}
