locals {
  registry       = jsondecode(file(var.registry_file))
  own_vpc        = var.vpc_id == ""
  vpc_id         = local.own_vpc ? aws_vpc.monitoring[0].id : var.vpc_id
  subnet_ids     = local.own_vpc ? [aws_subnet.monitoring[0].id] : var.subnet_ids
  active         = var.image_digest != ""
  parameter_name = split(":parameter", var.secrets_parameter_arn)[1]
  env = [
    { name = "AWS_DEFAULT_REGION", value = var.region },
    { name = "MONITORING_BUCKET", value = aws_s3_bucket.evidence.id },
    { name = "MONITORING_TABLE", value = aws_dynamodb_table.state.name },
    { name = "MONITORING_SECRETS_PARAMETER", value = local.parameter_name },
    { name = "MONITORING_REVISION", value = var.revision }
  ]
}

resource "terraform_data" "configuration" {
  lifecycle {
    precondition {
      condition     = !var.enabled || local.active
      error_message = "Build and push the image before enabling the scheduler."
    }
    precondition {
      condition     = local.own_vpc || length(var.subnet_ids) > 0
      error_message = "Existing VPC mode requires explicit existing subnets."
    }
    precondition {
      condition     = !var.enabled || (local.registry.expires_at > 0 && length(local.registry.reviewer_models) > 0 && length(local.registry.targets) > 0)
      error_message = "Activation requires a finite expiry, reviewer models, and approved targets."
    }
  }
}

resource "aws_s3_bucket" "evidence" {
  bucket        = "${var.name}-${var.account_id}-${var.region}"
  force_destroy = false
}
resource "aws_s3_bucket_public_access_block" "evidence" {
  bucket                  = aws_s3_bucket.evidence.id
  block_public_acls       = true
  block_public_policy     = !var.public_incident_log
  ignore_public_acls      = true
  restrict_public_buckets = !var.public_incident_log
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
  policy = jsonencode({ Version = "2012-10-17", Statement = concat([{
    Effect    = "Deny", Principal = "*", Action = "s3:*",
    Resource  = [aws_s3_bucket.evidence.arn, "${aws_s3_bucket.evidence.arn}/*"],
    Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }], var.public_incident_log ? [{
    Sid      = "PublicIncidentLog"
    Effect   = "Allow", Principal = "*", Action = "s3:GetObject",
    Resource = "${aws_s3_bucket.evidence.arn}/reviews/incidents/index.html"
  }] : []) })
  depends_on = [aws_s3_bucket_public_access_block.evidence]
}
resource "aws_s3_bucket_lifecycle_configuration" "evidence" {
  bucket = aws_s3_bucket.evidence.id
  rule {
    id     = "reviews-90-days"
    status = "Enabled"
    filter { prefix = "reviews/" }
    expiration { days = 90 }
    noncurrent_version_expiration { noncurrent_days = 90 }
  }
}
resource "aws_s3_object" "registry" {
  bucket       = aws_s3_bucket.evidence.id
  key          = "config/registry.json"
  source       = var.registry_file
  source_hash  = filemd5(var.registry_file)
  content_type = "application/json"
  depends_on   = [aws_s3_bucket_versioning.evidence]
}
resource "aws_dynamodb_table" "state" {
  name         = var.name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  attribute {
    name = "pk"
    type = "S"
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
  server_side_encryption { enabled = true }
}
resource "aws_ecr_repository" "monitoring" {
  name                 = var.name
  image_tag_mutability = "IMMUTABLE"
  image_scanning_configuration { scan_on_push = true }
}
resource "aws_cloudwatch_log_group" "jobs" {
  name              = "/crux/monitoring/${var.name}"
  retention_in_days = 30
}

resource "aws_vpc" "monitoring" {
  count                = local.own_vpc ? 1 : 0
  cidr_block           = "10.211.0.0/24"
  enable_dns_support   = true
  enable_dns_hostnames = true
}
resource "aws_internet_gateway" "monitoring" {
  count  = local.own_vpc ? 1 : 0
  vpc_id = aws_vpc.monitoring[0].id
}
data "aws_availability_zones" "available" { state = "available" }
resource "aws_subnet" "monitoring" {
  count                   = local.own_vpc ? 1 : 0
  vpc_id                  = aws_vpc.monitoring[0].id
  cidr_block              = "10.211.0.0/24"
  availability_zone       = data.aws_availability_zones.available.names[0]
  map_public_ip_on_launch = true
}
resource "aws_route_table" "monitoring" {
  count  = local.own_vpc ? 1 : 0
  vpc_id = aws_vpc.monitoring[0].id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.monitoring[0].id
  }
}
resource "aws_route_table_association" "monitoring" {
  count          = local.own_vpc ? 1 : 0
  subnet_id      = aws_subnet.monitoring[0].id
  route_table_id = aws_route_table.monitoring[0].id
}
resource "aws_security_group" "monitoring" {
  name        = var.name
  description = "Monitoring workers: no inbound access"
  vpc_id      = local.vpc_id
}
resource "aws_vpc_security_group_egress_rule" "https" {
  security_group_id = aws_security_group.monitoring.id
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = "0.0.0.0/0"
}
resource "aws_vpc_security_group_egress_rule" "inspection" {
  for_each          = toset(var.inspection_cidrs)
  security_group_id = aws_security_group.monitoring.id
  ip_protocol       = "tcp"
  from_port         = 22
  to_port           = 22
  cidr_ipv4         = each.value
}

resource "aws_iam_role" "instance" {
  name = "${var.name}-instance"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Principal = { Service = "ec2.amazonaws.com" }, Action = "sts:AssumeRole"
  }] })
}
resource "aws_iam_role_policy_attachment" "ecs_instance" {
  role       = aws_iam_role.instance.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonEC2ContainerServiceforEC2Role"
}
resource "aws_iam_instance_profile" "batch" {
  name = var.name
  role = aws_iam_role.instance.name
}
resource "aws_iam_role" "job" {
  for_each = toset(["discover", "review"])
  name     = "${var.name}-${each.key}"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Principal = { Service = "ecs-tasks.amazonaws.com" }, Action = "sts:AssumeRole",
    Condition = { StringEquals = { "aws:SourceAccount" = var.account_id },
    ArnLike = { "aws:SourceArn" = "arn:aws:ecs:${var.region}:${var.account_id}:*" } }
  }] })
}
resource "aws_iam_role_policy" "job" {
  for_each = aws_iam_role.job
  role     = each.value.id
  policy = jsonencode({ Version = "2012-10-17", Statement = concat([
    { Effect = "Allow", Action = ["ec2:DescribeInstances"], Resource = "*" },
    { Effect = "Allow", Action = ["s3:GetObject"], Resource = "${aws_s3_bucket.evidence.arn}/config/registry.json" },
    { Effect = "Allow", Action = ["s3:PutObject", "s3:GetObject"], Resource = "${aws_s3_bucket.evidence.arn}/${each.key == "discover" ? "inventory" : "reviews"}/*" },
    { Effect = "Allow", Action = each.key == "discover" ? ["dynamodb:Query"] : ["dynamodb:GetItem", "dynamodb:UpdateItem", "dynamodb:Scan"],
    Resource = [aws_dynamodb_table.state.arn, "${aws_dynamodb_table.state.arn}/index/*"] }
    ], each.key == "review" ? [
    { Effect = "Allow", Action = ["ssm:GetParameter"], Resource = var.secrets_parameter_arn }
    ] : [], each.key == "review" && var.secrets_kms_key_arn != "" ? [
    { Effect = "Allow", Action = ["kms:Decrypt"], Resource = var.secrets_kms_key_arn,
    Condition = { StringEquals = { "kms:ViaService" = "ssm.${var.region}.amazonaws.com", "kms:EncryptionContext:PARAMETER_ARN" = var.secrets_parameter_arn } } }
    ] : [], each.key == "review" && length(var.evidence_log_group_arns) > 0 ? [
    { Effect = "Allow", Action = ["logs:FilterLogEvents"], Resource = var.evidence_log_group_arns }
  ] : []) })
}
resource "aws_launch_template" "batch" {
  name_prefix = "${var.name}-"
  metadata_options {
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }
  block_device_mappings {
    device_name = "/dev/xvda"
    ebs {
      encrypted             = true
      volume_type           = "gp3"
      volume_size           = 30
      delete_on_termination = true
    }
  }
}
resource "aws_batch_compute_environment" "monitoring" {
  name = var.name
  type = "MANAGED"
  compute_resources {
    type                = "EC2"
    allocation_strategy = "BEST_FIT_PROGRESSIVE"
    min_vcpus           = 0
    max_vcpus           = 4
    instance_type       = ["m6i.large"]
    instance_role       = aws_iam_instance_profile.batch.arn
    subnets             = local.subnet_ids
    security_group_ids  = [aws_security_group.monitoring.id]
    ec2_configuration { image_type = "ECS_AL2023" }
    launch_template {
      launch_template_id = aws_launch_template.batch.id
      version            = tostring(aws_launch_template.batch.latest_version)
    }
    tags = { Name = "crux-monitor-worker", Project = "crux-monitoring" }
  }
  depends_on = [aws_iam_role_policy_attachment.ecs_instance]
}
resource "aws_batch_job_queue" "monitoring" {
  name     = var.name
  state    = "ENABLED"
  priority = 1
  compute_environment_order {
    order               = 1
    compute_environment = aws_batch_compute_environment.monitoring.arn
  }
}
resource "aws_batch_job_definition" "review" {
  count = local.active ? 1 : 0
  name  = "${var.name}-review"
  type  = "container"
  container_properties = jsonencode({
    image                = "${aws_ecr_repository.monitoring.repository_url}@${var.image_digest}",
    jobRoleArn           = aws_iam_role.job["review"].arn, user = "10001", readonlyRootFilesystem = true,
    resourceRequirements = [{ type = "VCPU", value = "1" }, { type = "MEMORY", value = "2048" }],
    environment          = local.env,
    logConfiguration     = { logDriver = "awslogs", options = { awslogs-group = aws_cloudwatch_log_group.jobs.name, awslogs-region = var.region, awslogs-stream-prefix = "review" } }
  })
  timeout { attempt_duration_seconds = 900 }
  retry_strategy { attempts = 2 }
}
resource "aws_batch_job_definition" "discover" {
  count = local.active ? 1 : 0
  name  = "${var.name}-discover"
  type  = "container"
  container_properties = jsonencode({
    image                = "${aws_ecr_repository.monitoring.repository_url}@${var.image_digest}",
    jobRoleArn           = aws_iam_role.job["discover"].arn, user = "10001", readonlyRootFilesystem = true,
    resourceRequirements = [{ type = "VCPU", value = "1" }, { type = "MEMORY", value = "1024" }],
    command              = ["python", "worker.py", "discover"],
    environment = concat(local.env, [
      { name = "MONITORING_QUEUE", value = aws_batch_job_queue.monitoring.arn },
      { name = "MONITORING_REVIEW_JOB", value = aws_batch_job_definition.review[0].arn }
    ]),
    logConfiguration = { logDriver = "awslogs", options = { awslogs-group = aws_cloudwatch_log_group.jobs.name, awslogs-region = var.region, awslogs-stream-prefix = "discover" } }
  })
  timeout { attempt_duration_seconds = 900 }
  retry_strategy { attempts = 2 }
}
resource "aws_iam_role_policy" "submit" {
  count = local.active ? 1 : 0
  role  = aws_iam_role.job["discover"].id
  policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Action = "batch:SubmitJob",
  Resource = [aws_batch_job_queue.monitoring.arn, aws_batch_job_definition.review[0].arn] }] })
}
resource "aws_sqs_queue" "operations" {
  name                      = "${var.name}-operations"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true
}
resource "aws_cloudwatch_event_rule" "failed" {
  name = "${var.name}-failed"
  event_pattern = jsonencode({ source = ["aws.batch"], "detail-type" = ["Batch Job State Change"],
  detail = { status = ["FAILED"], jobQueue = [aws_batch_job_queue.monitoring.arn] } })
}
resource "aws_cloudwatch_event_target" "failed" {
  rule = aws_cloudwatch_event_rule.failed.name
  arn  = aws_sqs_queue.operations.arn
}
resource "aws_sqs_queue_policy" "events" {
  queue_url = aws_sqs_queue.operations.url
  policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "events.amazonaws.com" },
    Action = "sqs:SendMessage", Resource = aws_sqs_queue.operations.arn,
  Condition = { ArnEquals = { "aws:SourceArn" = aws_cloudwatch_event_rule.failed.arn } } }] })
}
resource "aws_iam_role" "scheduler" {
  name = "${var.name}-scheduler"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Principal = { Service = "scheduler.amazonaws.com" }, Action = "sts:AssumeRole",
    Condition = { StringEquals = { "aws:SourceAccount" = var.account_id },
    ArnEquals = { "aws:SourceArn" = aws_scheduler_schedule_group.monitoring.arn } }
  }] })
}
resource "aws_scheduler_schedule_group" "monitoring" { name = var.name }
resource "aws_iam_role_policy" "scheduler" {
  count = local.active ? 1 : 0
  role  = aws_iam_role.scheduler.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = "batch:SubmitJob", Resource = [aws_batch_job_queue.monitoring.arn, aws_batch_job_definition.discover[0].arn] },
    { Effect = "Allow", Action = "sqs:SendMessage", Resource = aws_sqs_queue.operations.arn }
  ] })
}
resource "aws_scheduler_schedule" "monitoring" {
  count               = local.active ? 1 : 0
  name                = var.name
  group_name          = aws_scheduler_schedule_group.monitoring.name
  state               = var.enabled ? "ENABLED" : "DISABLED"
  schedule_expression = "rate(5 minutes)"
  # Without an explicit start, AWS uses now and rejects updates after the end date.
  start_date = var.enabled ? null : timeadd(var.schedule_end, "-1h")
  end_date   = var.schedule_end
  flexible_time_window { mode = "OFF" }
  target {
    arn      = "arn:aws:scheduler:::aws-sdk:batch:submitJob"
    role_arn = aws_iam_role.scheduler.arn
    input = jsonencode({ JobName = "${var.name}-discovery", JobQueue = aws_batch_job_queue.monitoring.arn,
    JobDefinition = aws_batch_job_definition.discover[0].arn })
    retry_policy {
      maximum_event_age_in_seconds = 300
      maximum_retry_attempts       = 2
    }
    dead_letter_config { arn = aws_sqs_queue.operations.arn }
  }
  depends_on = [aws_iam_role_policy.scheduler, terraform_data.configuration]
}
resource "aws_cloudwatch_metric_alarm" "operations" {
  alarm_name          = "${var.name}-operations-pending"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  dimensions          = { QueueName = aws_sqs_queue.operations.name }
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
}
