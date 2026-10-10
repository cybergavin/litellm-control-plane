# Remote state backend — configure to match your target cloud and storage.
# The example below uses an AWS S3 bucket; substitute the appropriate backend
# block for GCS, Azure Blob, Terraform Cloud, or a local file as needed.
#
# Required variables to set before running `tofu init`:
#   TF_STATE_BUCKET  — the S3 bucket name holding remote state
#   TF_STATE_REGION  — the AWS region of that bucket (e.g. us-east-1)
#
# Example (S3):
#   terraform {
#     backend "s3" {
#       bucket       = "<your-tfstate-bucket>"
#       key          = "litellm-control-plane/<environment>/tofu.tfstate"
#       encrypt      = true
#       region       = "<aws-region>"
#       use_lockfile = true
#     }
#   }
#
# When running in CI, pass -backend-config flags or set TF_CLI_ARGS_init.

terraform {
  backend "s3" {
    # Populated at init time via -backend-config or environment variables.
    # See README.md for full configuration guidance.
  }
}
