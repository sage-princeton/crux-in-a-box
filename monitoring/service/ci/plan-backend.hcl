# PR plans can read this state object; they cannot write state or acquire a lock.
bucket               = "sage-terraform-state-869937524494"
key                  = "crux/monitoring/terraform.tfstate"
region               = "us-east-1"
encrypt              = true
use_lockfile         = false
workspace_key_prefix = "crux/monitoring/workspaces"
kms_key_id           = "arn:aws:kms:us-east-1:869937524494:key/a87e0216-053f-48e4-8887-0aecb4d31c7f"
assume_role = {
  role_arn = "arn:aws:iam::869937524494:role/crux-monitoring-plan-state"
}
