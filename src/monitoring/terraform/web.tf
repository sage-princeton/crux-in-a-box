locals {
  web_service_configuration = templatefile("${path.module}/web-service.sh.tftpl", {
    region      = var.region, repository = aws_ecr_repository.monitoring.repository_url,
    image       = var.web_image_digest, proxy_image = var.proxy_image_digest,
    db_host     = aws_db_instance.monitoring.address,
    db_secret   = aws_db_instance.monitoring.master_user_secret[0].secret_arn,
    bucket      = aws_s3_bucket.evidence.id, document = aws_ssm_document.workspace.name,
    upload_role = aws_iam_role.workspace_upload.arn,
    origin      = local.web_origin, revision = var.revision
  })
}
# Preserve the public address and SAML origin during the data reset.
resource "aws_eip" "web" {
  #checkov:skip=CKV2_AWS_19:Count is fixed at one; aws_eip_association.web attaches this preserved address.
  count  = 1
  domain = "vpc"
  tags   = { Name = "crux-incident-web" }
}
resource "aws_eip_association" "web" {
  count         = 1
  instance_id   = aws_instance.web[0].id
  allocation_id = aws_eip.web[0].id
}
resource "aws_security_group" "web" {
  #checkov:skip=CKV2_AWS_5:Count is fixed at one; aws_instance.web attaches this security group.
  count       = 1
  name        = "crux-incident-web"
  description = "Authenticated monitoring website; administration through SSM"
  vpc_id      = var.vpc_id
}
resource "aws_vpc_security_group_ingress_rule" "web" {
  #checkov:skip=CKV_AWS_260:Public port 80 serves ACME challenges and redirects to HTTPS.
  for_each          = toset(["80", "443"])
  description       = "HTTPS website and ACME HTTP redirect"
  security_group_id = aws_security_group.web[0].id
  ip_protocol       = "tcp"
  from_port         = tonumber(each.key)
  to_port           = tonumber(each.key)
  cidr_ipv4         = "0.0.0.0/0"
}
resource "aws_vpc_security_group_egress_rule" "web" {
  count             = 1
  description       = "AWS APIs, model inference, telemetry, images and certificates"
  security_group_id = aws_security_group.web[0].id
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = "0.0.0.0/0"
}
resource "aws_iam_role" "web" {
  count = 1
  name  = "crux-incident-web"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Principal = { Service = "ec2.amazonaws.com" }, Action = "sts:AssumeRole"
  }] })
}
resource "aws_iam_instance_profile" "web" {
  count = 1
  name  = "crux-incident-web"
  role  = aws_iam_role.web[0].name
}
resource "aws_iam_role_policy_attachment" "web_ssm" {
  count      = 1
  role       = aws_iam_role.web[0].name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}
resource "aws_iam_role_policy" "web" {
  count = 1
  role  = aws_iam_role.web[0].id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["ecr:GetAuthorizationToken", "ec2:DescribeInstances"], Resource = "*" },
    { Effect = "Allow", Action = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability"], Resource = aws_ecr_repository.monitoring.arn },
    { Effect = "Allow", Action = ["ssm:GetParameter"], Resource = ["arn:aws:ssm:${var.region}:${var.account_id}:parameter/crux/monitoring/env", "arn:aws:ssm:${var.region}:${var.account_id}:parameter/crux/monitoring/web"] },
    { Effect = "Allow", Action = ["secretsmanager:GetSecretValue"], Resource = aws_db_instance.monitoring.master_user_secret[0].secret_arn },
    { Effect = "Allow", Action = ["s3:GetObject", "s3:GetObjectVersion", "s3:PutObject"], Resource = "${aws_s3_bucket.evidence.arn}/*" },
    { Effect = "Allow", Action = ["ssm:SendCommand"], Resource = aws_ssm_document.workspace.arn },
    { Effect = "Allow", Action = ["ssm:SendCommand"], Resource = "arn:aws:ec2:${var.region}:${var.account_id}:instance/*" },
    { Effect = "Allow", Action = ["ssm:GetCommandInvocation"], Resource = "*" },
    { Effect = "Allow", Action = ["sts:AssumeRole"], Resource = aws_iam_role.workspace_upload.arn }
  ] })
}
data "aws_ami" "web" {
  count       = 1
  most_recent = true
  owners      = ["amazon"]
  filter {
    name   = "name"
    values = ["al2023-ami-2023.*-kernel-6.1-x86_64"]
  }
  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}
resource "aws_instance" "web" {
  #checkov:skip=CKV_AWS_88:The authenticated HTTPS website intentionally uses the preserved public EIP.
  count                       = 1
  ami                         = data.aws_ami.web[0].id
  instance_type               = "t3.small"
  monitoring                  = true
  ebs_optimized               = true
  subnet_id                   = var.web_subnet_id
  vpc_security_group_ids      = [aws_security_group.web[0].id]
  iam_instance_profile        = aws_iam_instance_profile.web[0].name
  associate_public_ip_address = true
  user_data_replace_on_change = false
  metadata_options {
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }
  root_block_device {
    encrypted   = true
    volume_type = "gp3"
    volume_size = 16
  }
  user_data = templatefile("${path.module}/web-user-data.sh.tftpl", {
    service_configuration = local.web_service_configuration
  })
  tags = { Name = "crux-monitor-worker", CruxRole = "monitoring-web" }
  lifecycle { ignore_changes = [ami] }
  depends_on = [aws_iam_role_policy.web, aws_iam_role_policy_attachment.web_ssm]
}
