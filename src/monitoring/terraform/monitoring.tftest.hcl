mock_provider "aws" {
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
    condition     = jsondecode(aws_batch_job_definition.review[0].container_properties).readonlyRootFilesystem
    error_message = "Reviewer container root must be read-only."
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
