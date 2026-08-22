# providers.tf: provider pinning and state for the E1 Contact Centre AI service.
#
# Principle map (COMPLIANCE.md):
#   P-03 (single region / residency): every provider call is pinned to var.region, which is
#         SELECTED AT DEPLOY TIME and validated against an allowlist (variables.tf). There is
#         no global or multi-region default; the default is asia-southeast1, the one region
#         this service is licensed to hold contact audio, transcripts and audit records in.
#   P-02 (no lock-in): Terraform is the only place infrastructure is described. The app talks
#         to ports, never to these resources.
#
# Only the GA google provider is required. Nothing in this stack needs google-beta: the beta
# surface was a Doc1 requirement (Model Armor templates), and this service screens through the
# Hrz1 gateway over HTTPS instead of a managed guardrail resource, so carrying a second provider
# would be carrying an unused dependency.

terraform {
  required_version = ">= 1.9.0"

  # Partial backend. `terraform init -backend-config=...` supplies the reviewed state bucket
  # and the per-installation prefix, so neither is committed here and the module stays
  # reusable across installations. Keeping the declaration active is what makes accidental
  # local state impossible in a real runner; `-backend=false` is for offline validation only.
  backend "gcs" {}

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region # the selected, allowlisted region: pinned, never global
}
