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
    image_digest = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
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
  account_id            = "123456789012"
  registry_file         = "../registry.json.example"
  secrets_parameter_arn = "arn:aws:ssm:us-east-1:123456789012:parameter/crux/monitoring/env"
  schedule_end          = "2030-01-01T00:00:00Z"
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
