# outputs.tf: the values an operator needs after apply.
#
# Each one maps onto a setting the application reads (config/settings.yaml interpolates the
# CONTACT_* , HRZ_* and GCP_* names), so a deploy is "apply, then put these in the runtime
# environment". When the serving edge is enabled, Terraform already sets them on the Cloud Run
# service; the outputs are what a second-line reviewer reads, and what a hand-run or
# on-premises bridge needs.

output "project_id" {
  description = "The deployment project id."
  value       = var.project_id
}

output "region" {
  description = "The selected deploy region, validated against var.allowed_regions. App env: GCP_REGION."
  value       = var.region
}

output "residency_allowlist" {
  description = "The residency allowlist this plan validated the region against. The application validates the same list at settings load."
  value       = var.allowed_regions
}

# --------------------------------- KMS -------------------------------------- #
output "kms_key" {
  description = "Regional CMEK crypto key id, bound to logging, Firestore, Storage, Speech, Vertex AI and Cloud Run."
  value       = google_kms_crypto_key.contact.id
}

# ------------------------------- WORM logging ------------------------------- #
output "log_bucket" {
  description = "WORM audit log bucket id (locked when worm_locked = true)."
  value       = google_logging_project_bucket_config.worm_audit.id
}

output "audit_log_name" {
  description = "The log name the managed audit adapter writes to and the WORM sink routes. Fixed in the application, mirrored in naming.tf."
  value       = local.audit_log_name
}

output "worm_locked" {
  description = "Whether the audit bucket is irreversibly locked for the retention window. true is the compliant production posture."
  value       = var.worm_locked
}

output "audit_sink_writer_identity" {
  description = "Sink writer identity. Grant it bucket access if the destination is ever cross-project."
  value       = google_logging_project_sink.audit_to_worm.writer_identity
}

# ------------------------------ Data plane ---------------------------------- #
output "audio_bucket" {
  description = "Regional CMEK bucket the recogniser reads contact audio from (the gs:// prefix for an AudioRef uri)."
  value       = google_storage_bucket.audio.name
}

output "firestore_database" {
  description = "Firestore database backing the tenant-partitioned contact store, or null when var.create_firestore_database is false."
  value       = one(google_firestore_database.contacts[*].name)
}

# ----------------------------- Service account ------------------------------ #
output "app_service_account" {
  description = "The serving identity. It holds no exportable key, and org policy forbids creating one."
  value       = google_service_account.app.email
}

# --------------------------------- Modes ------------------------------------ #
output "modes_served" {
  description = <<-EOT
    The separately gated modes this apply serves, and the promotion bundle each stands on. An
    empty list is a valid posture: the platform can be deployed and reviewed before either mode
    is released, and with both off every mode route on every surface answers 503.
  EOT
  value = {
    for mode, bundle in {
      agent_assist = var.enable_agent_assist ? var.agent_assist_promotion_bundle : ""
      self_service = var.enable_self_service ? var.self_service_promotion_bundle : ""
    } : mode => bundle if bundle != ""
  }
}

# ------------------------------ Serving edge -------------------------------- #
output "service_origin" {
  description = "The production origin. Empty when production_edge_enabled is false."
  value       = var.production_edge_enabled ? "https://${var.service_domain}" : ""
}

output "api_service" {
  description = "Cloud Run service receiving only load-balancer and internal ingress."
  value       = try(google_cloud_run_v2_service.api[0].name, "")
}

output "edge_ip_address" {
  description = "Global anycast address for the edge. Point service_domain at it when dns_managed_zone is not set."
  value       = try(google_compute_global_address.edge[0].address, "")
}

output "iap_audience" {
  description = <<-EOT
    The exact value CONTACT_IAP_AUDIENCE must carry: the IAP-protected backend service this
    edge created. Set var.iap_audience to this after the first apply and apply again. Until
    then the service starts, stays health-checkable and refuses every end-user request with a
    503 naming the variable, which is the documented fail-closed behaviour rather than a gap.
  EOT
  value       = try("/projects/${data.google_project.this.number}/global/backendServices/${google_compute_backend_service.api[0].generated_id}", "")
}

output "iap_audience_configured" {
  description = "Whether this apply passed an audience to the service. False means end-user routes refuse with 503 until the second apply."
  value       = var.iap_audience != ""
}
