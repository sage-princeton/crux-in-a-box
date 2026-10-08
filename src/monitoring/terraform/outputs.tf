output "repository_url" { value = aws_ecr_repository.monitoring.repository_url }
output "evidence_bucket" { value = aws_s3_bucket.evidence.id }
output "state_table" { value = aws_dynamodb_table.incidents.name }
output "job_queue" { value = aws_batch_job_queue.monitoring.arn }
output "review_job" { value = try(aws_batch_job_definition.review[0].arn, null) }
output "discovery_job" { value = try(aws_batch_job_definition.discover[0].arn, null) }
output "inspection_security_group" { value = aws_security_group.monitoring.id }
output "operations_queue" { value = aws_sqs_queue.operations.url }
output "incident_log_url" {
  value = null
}
