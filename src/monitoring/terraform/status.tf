variable "status_provisioned" {
  type        = bool
  default     = false
  description = "Provision independent status infrastructure through the normal Terraform deployment. Provisioned status checks are always enabled."
}
variable "status_registry_file" {
  type        = string
  default     = ""
  description = "Non-secret status configuration; complete the example before provisioning. Terraform always sets enabled=true."
}
variable "status_secrets_parameter_arn" {
  type        = string
  default     = ""
  description = "Separate SecureString containing only approved status evidence/model credentials."
  validation {
    condition     = var.status_secrets_parameter_arn == "" || can(regex("^arn:aws:ssm:[a-z0-9-]+:[0-9]{12}:parameter/crux/status/", var.status_secrets_parameter_arn))
    error_message = "Use a dedicated /crux/status/ parameter."
  }
}
variable "status_secrets_kms_key_arn" {
  type    = string
  default = ""
}
variable "status_evidence_log_group_arns" {
  type        = list(string)
  default     = []
  description = "Explicit CloudWatch groups approved for status checks, independently of incident monitoring."
}

locals {
  status_config = merge(
    jsondecode(file(var.status_registry_file == "" ? "${path.module}/../status.json.example" : var.status_registry_file)),
    { enabled = true }
  )
  status_active = var.status_provisioned && local.active
  status_end    = local.status_config.expires_at > 0 ? timeadd("1970-01-01T00:00:00Z", "${local.status_config.expires_at}s") : null
  status_env = var.status_provisioned ? [
    { name = "AWS_DEFAULT_REGION", value = var.region },
    { name = "STATUS_TABLE", value = aws_dynamodb_table.status[0].name },
    { name = "STATUS_BUCKET", value = aws_s3_bucket.evidence.id },
    { name = "STATUS_SECRETS_PARAMETER", value = trimprefix(var.status_secrets_parameter_arn, "arn:aws:ssm:${var.region}:${var.account_id}:parameter") },
    { name = "MONITORING_REVISION", value = var.revision }
  ] : []
}

resource "terraform_data" "status_configuration" {
  count = var.status_provisioned ? 1 : 0
  lifecycle {
    precondition {
      condition     = !var.status_provisioned || var.status_secrets_parameter_arn != ""
      error_message = "Provisioning requires a dedicated status credentials parameter."
    }
    precondition {
      condition = (local.status_active && local.status_config.expires_at >= 0 &&
        (local.status_config.expires_at == 0 ? true : timecmp(local.status_end, timestamp()) > 0) && (length(local.status_config.targets) > 0 || try(local.status_config.auto_register_runs, false)) &&
        local.status_config.inference_budget_usd > 0 && local.status_config.inference_budget_usd <= 500 &&
      local.status_config.sweep_model != "" && local.status_config.summary_model != "")
      error_message = "Status activation requires its infrastructure, image, targets, model pair and bounded budget; expiry must be zero (continuous) or in the future."
    }
    precondition {
      condition     = local.status_config.interval_seconds >= 300 && local.status_config.interval_seconds <= 3600 && local.status_config.interval_seconds % 60 == 0 && local.status_config.stale_seconds >= local.status_config.interval_seconds
      error_message = "Status cadence must be 5–60 whole minutes; staleness must be at least one interval."
    }
  }
}

resource "aws_kms_key" "status" {
  count                   = var.status_provisioned ? 1 : 0
  description             = "${var.name} independent workload status"
  enable_key_rotation     = true
  deletion_window_in_days = 30
  policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Sid       = "AccountAdministration", Effect = "Allow",
    Principal = { AWS = "arn:aws:iam::${var.account_id}:root" },
    Action    = "kms:*", Resource = "*"
  }] })
  lifecycle { prevent_destroy = true }
}
resource "aws_dynamodb_table" "status" {
  count                       = var.status_provisioned ? 1 : 0
  name                        = "${var.name}-status"
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "pk"
  range_key                   = "sk"
  deletion_protection_enabled = true
  attribute {
    name = "pk"
    type = "S"
  }
  attribute {
    name = "sk"
    type = "S"
  }
  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }
  point_in_time_recovery { enabled = true }
  server_side_encryption {
    enabled     = true
    kms_key_arn = aws_kms_key.status[0].arn
  }
}
resource "aws_s3_object" "status_registry" {
  count        = var.status_provisioned ? 1 : 0
  bucket       = aws_s3_bucket.evidence.id
  key          = "config/status.json"
  content      = jsonencode(local.status_config)
  content_type = "application/json"
  depends_on   = [aws_s3_bucket_versioning.evidence, terraform_data.status_configuration]
}
resource "aws_iam_role" "status_job" {
  for_each = var.status_provisioned ? toset(["discover", "check"]) : toset([])
  name     = "${var.name}-status-${each.key}"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Principal = { Service = "ecs-tasks.amazonaws.com" }, Action = "sts:AssumeRole",
    Condition = { StringEquals = { "aws:SourceAccount" = var.account_id },
    ArnLike = { "aws:SourceArn" = "arn:aws:ecs:${var.region}:${var.account_id}:*" } }
  }] })
}
resource "aws_iam_role_policy" "status_job" {
  for_each = aws_iam_role.status_job
  role     = each.value.id
  policy = jsonencode({ Version = "2012-10-17", Statement = concat([
    { Effect = "Allow", Action = ["ec2:DescribeInstances"], Resource = "*" },
    { Effect = "Allow", Action = ["s3:GetObject"], Resource = "${aws_s3_bucket.evidence.arn}/config/status.json" },
    { Effect = "Allow", Action = ["dynamodb:GetItem", "dynamodb:Query", "dynamodb:PutItem", "dynamodb:UpdateItem"], Resource = aws_dynamodb_table.status[0].arn },
    { Effect    = "Allow", Action = ["kms:Decrypt", "kms:Encrypt", "kms:GenerateDataKey", "kms:DescribeKey"], Resource = aws_kms_key.status[0].arn,
      Condition = { StringEquals = { "kms:ViaService" = "dynamodb.${var.region}.amazonaws.com", "kms:CallerAccount" = var.account_id } }
    }
    ], each.key == "check" ? [
    { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject"], Resource = "${aws_s3_bucket.evidence.arn}/status_checks/*" },
    { Effect = "Allow", Action = ["ssm:GetParameter"], Resource = var.status_secrets_parameter_arn }
    ] : [], each.key == "discover" ? [
    { Effect = "Allow", Action = ["batch:SubmitJob"], Resource = [aws_batch_job_queue.status[0].arn, "arn:aws:batch:${var.region}:${var.account_id}:job-definition/${var.name}-status-check", "arn:aws:batch:${var.region}:${var.account_id}:job-definition/${var.name}-status-check:*"] }
    ] : [], each.key == "check" && length(var.status_evidence_log_group_arns) > 0 ? [
    { Effect = "Allow", Action = ["logs:FilterLogEvents"], Resource = var.status_evidence_log_group_arns }
    ] : [], each.key == "check" && var.status_secrets_kms_key_arn != "" ? [
    { Effect    = "Allow", Action = ["kms:Decrypt"], Resource = var.status_secrets_kms_key_arn,
      Condition = { StringEquals = { "kms:ViaService" = "ssm.${var.region}.amazonaws.com", "kms:EncryptionContext:PARAMETER_ARN" = var.status_secrets_parameter_arn } }
    }
  ] : []) })
}
resource "aws_iam_role_policy" "web_status" {
  count = var.status_provisioned && var.web_enabled ? 1 : 0
  role  = aws_iam_role.web[0].id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect    = "Allow", Action = ["dynamodb:GetItem", "dynamodb:Query"], Resource = aws_dynamodb_table.status[0].arn,
      Condition = { "ForAllValues:StringLike" = { "dynamodb:LeadingKeys" = ["FLEET", "STATUS#*"] } }
    },
    { Effect    = "Allow", Action = ["kms:Decrypt", "kms:DescribeKey"], Resource = aws_kms_key.status[0].arn,
      Condition = { StringEquals = { "kms:ViaService" = "dynamodb.${var.region}.amazonaws.com", "kms:CallerAccount" = var.account_id } }
    }
  ] })
}
resource "aws_iam_role_policy" "enrollment_status" {
  count = var.status_provisioned ? 1 : 0
  role  = aws_iam_role.job["review"].id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect    = "Allow", Action = ["dynamodb:Query"], Resource = aws_dynamodb_table.status[0].arn,
      Condition = { "ForAllValues:StringEquals" = { "dynamodb:LeadingKeys" = ["FLEET"] } }
    },
    { Effect    = "Allow", Action = ["kms:Decrypt", "kms:DescribeKey"], Resource = aws_kms_key.status[0].arn,
      Condition = { StringEquals = { "kms:ViaService" = "dynamodb.${var.region}.amazonaws.com", "kms:CallerAccount" = var.account_id } }
    }
  ] })
}
resource "aws_batch_job_queue" "status" {
  count    = var.status_provisioned ? 1 : 0
  name     = "${var.name}-status"
  state    = "ENABLED"
  priority = 1
  compute_environment_order {
    order               = 1
    compute_environment = aws_batch_compute_environment.monitoring.arn
  }
}
resource "aws_batch_job_definition" "status" {
  for_each = local.status_active ? toset(["discover", "check"]) : toset([])
  name     = "${var.name}-status-${each.key}"
  type     = "container"
  container_properties = jsonencode({
    image                = "${aws_ecr_repository.monitoring.repository_url}@${var.image_digest}",
    jobRoleArn           = aws_iam_role.status_job[each.key].arn, user = "10001", readonlyRootFilesystem = true,
    resourceRequirements = [{ type = "VCPU", value = "1" }, { type = "MEMORY", value = "2048" }],
    command              = ["python", "status_worker.py", each.key],
    environment = concat(local.status_env, each.key == "discover" ? [
      { name = "STATUS_QUEUE", value = aws_batch_job_queue.status[0].arn },
      { name = "STATUS_JOB", value = "${var.name}-status-check" }
    ] : []),
    logConfiguration = { logDriver = "awslogs", options = { awslogs-group = aws_cloudwatch_log_group.jobs.name, awslogs-region = var.region, awslogs-stream-prefix = "status-${each.key}" } }
  })
  timeout { attempt_duration_seconds = 900 }
  retry_strategy { attempts = 2 }
}
resource "aws_scheduler_schedule_group" "status" {
  count = var.status_provisioned ? 1 : 0
  name  = "${var.name}-status"
}
resource "aws_iam_role" "status_scheduler" {
  count = var.status_provisioned ? 1 : 0
  name  = "${var.name}-status-scheduler"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Principal = { Service = "scheduler.amazonaws.com" }, Action = "sts:AssumeRole",
    Condition = { StringEquals = { "aws:SourceAccount" = var.account_id },
    ArnEquals = { "aws:SourceArn" = aws_scheduler_schedule_group.status[0].arn } }
  }] })
}
resource "aws_iam_role_policy" "status_scheduler" {
  count = var.status_provisioned ? 1 : 0
  role  = aws_iam_role.status_scheduler[0].id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["batch:SubmitJob"], Resource = [aws_batch_job_queue.status[0].arn, "arn:aws:batch:${var.region}:${var.account_id}:job-definition/${var.name}-status-discover:*"] },
    { Effect = "Allow", Action = ["sqs:SendMessage"], Resource = aws_sqs_queue.status_operations[0].arn }
  ] })
}
resource "aws_scheduler_schedule" "status" {
  #checkov:skip=CKV_AWS_297:AWS-managed encryption protects this schedule; payload contains only non-secret Batch resource names.
  count               = local.status_active ? 1 : 0
  name                = "${var.name}-status"
  group_name          = aws_scheduler_schedule_group.status[0].name
  state               = "ENABLED"
  schedule_expression = "rate(${local.status_config.interval_seconds / 60} minutes)"
  end_date            = local.status_end
  flexible_time_window { mode = "OFF" }
  target {
    arn      = "arn:aws:scheduler:::aws-sdk:batch:submitJob"
    role_arn = aws_iam_role.status_scheduler[0].arn
    input = jsonencode({ JobName = "${var.name}-status-discovery", JobQueue = aws_batch_job_queue.status[0].arn,
    JobDefinition = aws_batch_job_definition.status["discover"].arn })
    retry_policy {
      maximum_event_age_in_seconds = 300
      maximum_retry_attempts       = 2
    }
    dead_letter_config { arn = aws_sqs_queue.status_operations[0].arn }
  }
  depends_on = [aws_iam_role_policy.status_scheduler, terraform_data.status_configuration]
}
resource "aws_sqs_queue" "status_operations" {
  count                     = var.status_provisioned ? 1 : 0
  name                      = "${var.name}-status-operations"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true
}
resource "aws_cloudwatch_event_rule" "status_failed" {
  count = var.status_provisioned ? 1 : 0
  name  = "${var.name}-status-failed"
  event_pattern = jsonencode({ source = ["aws.batch"], "detail-type" = ["Batch Job State Change"],
  detail = { status = ["FAILED"], jobQueue = [aws_batch_job_queue.status[0].arn] } })
}
resource "aws_cloudwatch_event_target" "status_failed" {
  count = var.status_provisioned ? 1 : 0
  rule  = aws_cloudwatch_event_rule.status_failed[0].name
  arn   = aws_sqs_queue.status_operations[0].arn
}
resource "aws_sqs_queue_policy" "status_operations" {
  count     = var.status_provisioned ? 1 : 0
  queue_url = aws_sqs_queue.status_operations[0].url
  policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect    = "Allow", Principal = { Service = "events.amazonaws.com" }, Action = "sqs:SendMessage",
    Resource  = aws_sqs_queue.status_operations[0].arn,
    Condition = { ArnEquals = { "aws:SourceArn" = aws_cloudwatch_event_rule.status_failed[0].arn } }
  }] })
}
resource "aws_cloudwatch_metric_alarm" "status_operations" {
  count               = var.status_provisioned ? 1 : 0
  alarm_name          = "${var.name}-status-operations-pending"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  dimensions          = { QueueName = aws_sqs_queue.status_operations[0].name }
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
}
output "status_table" { value = try(aws_dynamodb_table.status[0].name, null) }
output "status_queue" { value = try(aws_batch_job_queue.status[0].arn, null) }
output "status_discovery_job" { value = try(aws_batch_job_definition.status["discover"].arn, null) }
