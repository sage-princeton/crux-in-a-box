mock_provider "aws" {
  mock_resource "aws_kms_key" {
    defaults = { arn = "arn:aws:kms:us-east-1:123456789012:key/12345678-1234-1234-1234-123456789012" }
  }
  mock_resource "aws_cloudwatch_log_group" {
    defaults = { arn = "arn:aws:logs:us-east-1:123456789012:log-group:/crux/monitoring/test" }
  }
  mock_resource "aws_iam_instance_profile" {
    defaults = { arn = "arn:aws:iam::123456789012:instance-profile/monitoring" }
  }
  mock_resource "aws_iam_role" {
    defaults = { arn = "arn:aws:iam::123456789012:role/monitoring" }
  }
  mock_resource "aws_batch_compute_environment" {
    defaults = { arn = "arn:aws:batch:us-east-1:123456789012:compute-environment/monitoring" }
  }
  mock_resource "aws_batch_job_queue" {
    defaults = { arn = "arn:aws:batch:us-east-1:123456789012:job-queue/monitoring" }
  }
  mock_resource "aws_batch_job_definition" {
    defaults = { arn = "arn:aws:batch:us-east-1:123456789012:job-definition/monitoring:1" }
  }
  mock_resource "aws_sqs_queue" {
    defaults = { arn = "arn:aws:sqs:us-east-1:123456789012:monitoring", url = "https://sqs.us-east-1.amazonaws.com/123456789012/monitoring" }
  }
  mock_resource "aws_cloudwatch_event_rule" {
    defaults = { arn = "arn:aws:events:us-east-1:123456789012:rule/monitoring" }
  }
  mock_resource "aws_scheduler_schedule_group" {
    defaults = { arn = "arn:aws:scheduler:us-east-1:123456789012:schedule-group/monitoring" }
  }
  mock_data "aws_availability_zones" {
    defaults = { names = ["us-east-1a"] }
  }
  mock_data "aws_ami" {
    defaults = { id = "ami-12345678" }
  }
  mock_resource "aws_eip" {
    defaults = { public_ip = "192.0.2.10" }
  }
}

run "web_service_and_operator_state_are_separate_from_workers" {
  command = apply
  variables {
    proxy_image_digest     = "sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
    web_enabled            = true
    web_image_digest       = "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    incident_state_enabled = true
  }
  assert {
    condition     = aws_instance.web[0].tags.Name == "crux-incident-web" && aws_instance.web[0].metadata_options[0].http_tokens == "required"
    error_message = "The web service must be separate and require IMDSv2."
  }
  assert {
    condition     = toset([for rule in aws_vpc_security_group_ingress_rule.web : rule.from_port]) == toset([80, 443])
    error_message = "Only web traffic may enter the public host; no SSH whitelist."
  }
  assert {
    condition     = aws_dynamodb_table.incidents.deletion_protection_enabled && aws_dynamodb_table.incidents.range_key == "sk"
    error_message = "Incident state must have durable keyed history and deletion protection."
  }
  assert {
    condition     = !strcontains(aws_iam_role_policy.incident_ingestion.policy, "SESSION#") && !strcontains(aws_iam_role_policy.incident_ingestion.policy, "LOGIN#")
    error_message = "Monitoring workers must not mint operator sessions."
  }
  assert {
    condition     = !strcontains(aws_iam_role_policy.job["review"].policy, "dynamodb:Scan")
    error_message = "Stateful workers must use keyed queries rather than legacy table scans."
  }
}

run "immutable_workers_remain_disabled" {
  command = apply
  variables {
    image_digest  = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    registry_file = "../registry.json"
  }
  assert {
    condition     = length(terraform_data.status_configuration) == 0 && length(aws_dynamodb_table.status) == 0 && length(aws_batch_job_definition.status) == 0
    error_message = "Ordinary releases must not create status infrastructure before operator bootstrap."
  }
  assert {
    condition     = aws_scheduler_schedule.monitoring[0].state == "DISABLED"
    error_message = "Adding an image must not implicitly activate monitoring."
  }
  assert {
    condition     = timecmp(aws_scheduler_schedule.monitoring[0].start_date, aws_scheduler_schedule.monitoring[0].end_date) < 0
    error_message = "A disabled schedule needs an explicit start before its end so expired runs remain updateable."
  }
  assert {
    condition     = jsondecode(aws_batch_job_definition.review[0].container_properties).readonlyRootFilesystem
    error_message = "Reviewer container root must be read-only."
  }
  assert {
    condition     = jsondecode(aws_iam_role_policy.submit[0].policy).Statement[0].Resource[1] == "arn:aws:batch:${var.region}:${var.account_id}:job-definition/${var.name}-review:*"
    error_message = "New releases must work without CI mutating IAM; submission stays limited to the named reviewer definition."
  }
}

variables {
  continuous_fleet_monitoring = false
  status_provisioned          = false
  status_registry_file        = ""
  account_id                  = "123456789012"
  registry_file               = "../registry.json.example"
  secrets_parameter_arn       = "arn:aws:ssm:us-east-1:123456789012:parameter/crux/monitoring/env"
  schedule_end                = "2030-01-01T00:00:00Z"
}

run "bootstrap_without_workers" {
  command = apply
  assert {
    condition     = length(aws_batch_job_definition.review) == 0 && length(aws_scheduler_schedule.monitoring) == 0
    error_message = "Bootstrap must not start jobs before an immutable image is supplied."
  }
  assert {
    condition     = aws_batch_compute_environment.monitoring.compute_resources[0].min_vcpus == 0
    error_message = "Idle monitoring must scale to zero."
  }
  assert {
    condition     = alltrue([for p in jsondecode(aws_iam_role_policy.job["review"].policy).Statement : alltrue([for a in p.Action : !contains(["ssm:SendCommand", "ec2:RunInstances", "ec2:ModifyInstanceAttribute", "ec2:*", "ssm:*", "batch:SubmitJob"], a)])])
    error_message = "The reviewer must not mutate EC2 targets or dispatch commands/jobs."
  }
  assert {
    condition     = aws_s3_bucket_public_access_block.evidence.block_public_policy && aws_s3_bucket_public_access_block.evidence.restrict_public_buckets
    error_message = "Evidence must not be publicly readable."
  }
}

run "activation_requires_image_and_registry" {
  command = plan
  variables { enabled = true }
  expect_failures = [terraform_data.configuration]
}

run "legacy_public_input_cannot_reopen_s3" {
  command = apply
  variables { public_incident_log = true }
  assert {
    condition     = length([for statement in jsondecode(aws_s3_bucket_policy.tls.policy).Statement : statement if statement.Effect == "Allow"]) == 0
    error_message = "No object, including legacy HTML, may have a public policy grant."
  }
  assert {
    condition     = aws_s3_bucket_public_access_block.evidence.block_public_acls && aws_s3_bucket_public_access_block.evidence.ignore_public_acls && aws_s3_bucket_public_access_block.evidence.block_public_policy && aws_s3_bucket_public_access_block.evidence.restrict_public_buckets
    error_message = "All four S3 public-access protections must remain enabled."
  }
}

run "existing_vpc_is_not_reconfigured" {
  command = apply
  variables {
    vpc_id     = "vpc-12345678"
    subnet_ids = ["subnet-12345678"]
  }
  assert {
    condition     = length(aws_default_security_group.monitoring) == 0 && length(aws_flow_log.monitoring) == 0
    error_message = "Existing shared VPC security groups and network logging remain under their owner's control."
  }
  assert {
    condition     = aws_dynamodb_table.incidents.deletion_protection_enabled && aws_dynamodb_table.incidents.server_side_encryption[0].kms_key_arn == aws_kms_key.state.arn
    error_message = "The shared table must use the managed state key and protect review state from deletion."
  }
  assert {
    condition     = jsondecode(aws_iam_role_policy.state_encryption["review"].policy).Statement[0].Condition.StringEquals["kms:ViaService"] == "dynamodb.us-east-1.amazonaws.com"
    error_message = "Worker key use must be limited to DynamoDB, not arbitrary KMS decryption."
  }
}

run "shared_state_keeps_worker_access_out_of_operator_sessions" {
  command = apply
  assert {
    condition     = alltrue([for statement in jsondecode(aws_iam_role_policy.job["review"].policy).Statement : can(statement.Condition["ForAllValues:StringLike"]["dynamodb:LeadingKeys"]) if contains(statement.Action, "dynamodb:UpdateItem")])
    error_message = "Every worker state write must remain constrained by record prefix on the shared table."
  }
  assert {
    condition     = toset([for index in aws_dynamodb_table.incidents.global_secondary_index : index.name]) == toset(["instance-incidents", "status-updated"])
    error_message = "The shared table must support both incident pages and pending review discovery."
  }
}

run "status_has_its_own_storage_roles_queue_and_enabled_schedule" {
  command = apply
  variables {
    status_provisioned           = true
    status_registry_file         = "../tests/fixtures/status.json"
    status_secrets_parameter_arn = "arn:aws:ssm:us-east-1:123456789012:parameter/crux/status/env"
    image_digest                 = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    web_enabled                  = true
    web_image_digest             = "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    proxy_image_digest           = "sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
  }
  assert {
    condition     = aws_dynamodb_table.status[0].name != aws_dynamodb_table.incidents.name && aws_dynamodb_table.status[0].deletion_protection_enabled && aws_dynamodb_table.status[0].point_in_time_recovery[0].enabled
    error_message = "Status needs a separate protected table and recovery policy."
  }
  assert {
    condition     = aws_scheduler_schedule.status[0].state == "ENABLED" && jsondecode(aws_s3_object.status_registry[0].content).enabled && aws_scheduler_schedule.monitoring[0].state == "DISABLED" && aws_batch_job_queue.status[0].name != aws_batch_job_queue.monitoring.name
    error_message = "Provisioned status must enable its schedule and worker config even when the input says disabled, independently of incident scheduling."
  }
  assert {
    condition     = alltrue([for s in jsondecode(aws_iam_role_policy.status_job["check"].policy).Statement : s.Resource == aws_dynamodb_table.status[0].arn if contains(s.Action, "dynamodb:UpdateItem")]) && !strcontains(aws_iam_role_policy.status_job["check"].policy, "ssm:SendCommand") && !strcontains(aws_iam_role_policy.status_job["check"].policy, "batch:SubmitJob")
    error_message = "Status checkers can write only their own table and cannot dispatch workload commands or jobs."
  }
  assert {
    condition = contains(
      one([for s in jsondecode(aws_iam_role_policy.status_job["discover"].policy).Statement : s.Resource if contains(s.Action, "batch:SubmitJob")]),
      "arn:aws:batch:${var.region}:${var.account_id}:job-definition/${one([for env in jsondecode(aws_batch_job_definition.status["discover"].container_properties).environment : env.value if env.name == "STATUS_JOB"])}"
    )
    error_message = "Discovery must be authorized to submit the unversioned job definition passed in STATUS_JOB."
  }
  assert {
    condition     = !strcontains(aws_iam_role_policy.web_status[0].policy, "dynamodb:PutItem") && !strcontains(aws_iam_role_policy.web_status[0].policy, "dynamodb:UpdateItem") && !strcontains(aws_iam_role_policy.web_status[0].policy, "dynamodb:DeleteItem")
    error_message = "The website must have read-only status access."
  }
  assert {
    condition     = !strcontains(aws_batch_job_definition.status["check"].container_properties, "MONITORING_TABLE") && strcontains(aws_batch_job_definition.status["check"].container_properties, "STATUS_TABLE") && strcontains(local.web_service_configuration, "STATUS_TABLE=")
    error_message = "Status jobs and the web service must use explicit independent table configuration."
  }
}

run "status_provisioning_requires_complete_configuration" {
  command = plan
  variables {
    status_provisioned           = true
    status_secrets_parameter_arn = "arn:aws:ssm:us-east-1:123456789012:parameter/crux/status/env"
    image_digest                 = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  }
  expect_failures = [terraform_data.status_configuration[0]]
}

run "production_status_runs_continuously_in_a_separate_table" {
  command = apply
  variables {
    account_id                   = "881004720495"
    status_provisioned           = true
    status_registry_file         = "../status.production.json"
    status_secrets_parameter_arn = "arn:aws:ssm:us-east-1:881004720495:parameter/crux/status/env"
    image_digest                 = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    continuous_fleet_monitoring  = true
    registry_file                = "../registry.json"
    schedule_end                 = "2026-10-02T19:33:56Z"
  }
  assert {
    condition     = aws_scheduler_schedule.status[0].state == "ENABLED" && aws_scheduler_schedule.status[0].end_date == null && aws_scheduler_schedule.status[0].schedule_expression == "rate(15 minutes)"
    error_message = "Production status must run every 15 minutes without an automatic stop date."
  }
  assert {
    condition     = aws_dynamodb_table.status[0].name != aws_dynamodb_table.incidents.name && length(local.status_config.targets) > 0
    error_message = "Configured workloads must use independent status storage."
  }
  assert {
    condition     = aws_scheduler_schedule.monitoring[0].state == "ENABLED" && aws_scheduler_schedule.monitoring[0].end_date == null && jsondecode(aws_s3_object.registry.content).expires_at == 0 && jsondecode(aws_s3_object.registry.content).fleet.langfuse_by_name && local.status_config.auto_register_runs
    error_message = "Production must enroll new runs in both monitors even when the old incident registry and schedule have expired."
  }
  assert {
    condition     = jsondecode(aws_s3_object.registry.content).fleet.exclude_instance_ids == local.registry_input.fleet.exclude_instance_ids && jsondecode(aws_s3_object.registry.content).inference_budget_usd == local.registry_input.inference_budget_usd
    error_message = "Continuous discovery must retain operator exclusions and the inference budget."
  }
  assert {
    condition     = jsondecode(aws_iam_role_policy.enrollment_status[0].policy).Statement[0].Action == ["dynamodb:Query"] && jsondecode(aws_iam_role_policy.enrollment_status[0].policy).Statement[0].Condition["ForAllValues:StringEquals"]["dynamodb:LeadingKeys"] == ["FLEET"] && contains([for env in jsondecode(aws_batch_job_definition.review[0].container_properties).environment : env.name], "MONITORING_STATUS_TABLE")
    error_message = "Enrollment notices need only read status inventory; incident workers must not write status results."
  }
}
