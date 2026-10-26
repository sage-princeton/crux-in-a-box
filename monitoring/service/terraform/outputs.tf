output "repository_url" { value = aws_ecr_repository.monitoring.repository_url }
output "evidence_bucket" { value = aws_s3_bucket.evidence.id }
output "incident_web_url" { value = local.web_origin }
output "incident_web_instance" { value = aws_instance.web[0].id }
output "incident_web_configuration" { value = local.web_service_configuration }
output "postgres_address" { value = aws_db_instance.monitoring.address }
output "workspace_document" { value = aws_ssm_document.workspace.name }
