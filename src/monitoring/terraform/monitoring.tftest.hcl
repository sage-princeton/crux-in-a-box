mock_provider "aws" {
  mock_data "aws_subnets" {
    defaults = { ids = ["subnet-12345678", "subnet-abcdef12"] }
  }
  mock_data "aws_ami" {
    defaults = { id = "ami-12345678" }
  }
  mock_resource "aws_eip" {
    defaults = { public_ip = "34.193.109.221" }
  }
  mock_resource "aws_iam_role" {
    defaults = { arn = "arn:aws:iam::881004720495:role/monitoring" }
  }
  mock_resource "aws_iam_instance_profile" {
    defaults = { arn = "arn:aws:iam::881004720495:instance-profile/monitoring" }
  }
  mock_resource "aws_db_instance" {
    defaults = {
      address = "monitoring.example.rds.amazonaws.com"
      master_user_secret = [{
        secret_arn    = "arn:aws:secretsmanager:us-east-1:881004720495:secret:monitoring-fixture"
        secret_status = "active"
        kms_key_id    = "arn:aws:kms:us-east-1:881004720495:key/12345678-1234-1234-1234-123456789012"
      }]
    }
  }
}

variables {
  revision           = "test"
  web_image_digest   = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  proxy_image_digest = "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
}

run "private_postgres_with_managed_credentials_and_backups" {
  command = apply
  assert {
    condition     = aws_db_instance.monitoring.engine == "postgres" && !aws_db_instance.monitoring.publicly_accessible && aws_db_instance.monitoring.storage_encrypted && aws_db_instance.monitoring.manage_master_user_password && aws_db_instance.monitoring.backup_retention_period == 7 && aws_db_instance.monitoring.deletion_protection
    error_message = "Monitoring state must use private encrypted PostgreSQL with managed credentials and backups."
  }
  assert {
    condition     = aws_vpc_security_group_ingress_rule.postgres.referenced_security_group_id == aws_security_group.web[0].id && aws_vpc_security_group_ingress_rule.postgres.from_port == 5432
    error_message = "Only the monitor host may connect to PostgreSQL."
  }
  assert {
    condition     = anytrue([for parameter in aws_db_parameter_group.monitoring.parameter : parameter.name == "rds.force_ssl" && parameter.value == "1"])
    error_message = "RDS connections must require TLS."
  }
}

run "whole_workspace_copy_and_migrations_before_startup" {
  command = apply
  assert {
    condition     = toset([for policy in aws_iam_role_policy.run_ssm : policy.role]) == var.workspace_roles
    error_message = "Every configured workspace role needs SSM management for full workspace copies."
  }
  assert {
    condition     = jsondecode(aws_ssm_document.workspace.content).parameters.Credentials.interpolationType == "ENV_VAR" && strcontains(jsondecode(aws_ssm_document.workspace.content).mainSteps[0].inputs.runCommand[1], "archive.add(root, arcname=\"workspace\")")
    error_message = "Workspace exports must archive the whole directory and pass URLs through environment variables."
  }
  assert {
    condition     = strcontains(local.web_service_configuration, "alembic upgrade head") && strcontains(local.web_service_configuration, "python worker.py run") && !strcontains(local.web_service_configuration, "STATUS_TABLE=")
    error_message = "Web and worker must share migrated PostgreSQL state."
  }
  assert {
    condition     = aws_instance.web[0].tags.Name == "crux-monitor-worker" && aws_instance.web[0].metadata_options[0].http_tokens == "required"
    error_message = "The monitor must exclude itself by name and require IMDSv2."
  }
  assert {
    condition     = aws_s3_bucket_public_access_block.evidence.block_public_policy && aws_s3_bucket_public_access_block.evidence.restrict_public_buckets && aws_s3_bucket_versioning.evidence.versioning_configuration[0].status == "Enabled"
    error_message = "Whole workspace snapshots must remain private and versioned."
  }
}
