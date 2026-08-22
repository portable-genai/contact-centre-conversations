# logging_worm.tf: the WORM audit trail, a locked Cloud Logging bucket plus sink.
#
# Principle map (COMPLIANCE.md):
#   P-07 (auditable by design, rule R2): the audit stream is routed to a Cloud Logging bucket
#         whose retention is var.retention_days and whose lock (var.worm_locked) makes it
#         Write-Once-Read-Many. The managed audit adapter writes already-redacted AuditEvents
#         into it, each one tagged with the mode that produced it. The offline profile earns
#         tamper evidence with a hash chain and an external head anchor; a managed WORM sink
#         needs no anchor because it provides non-rewritability itself, which is why the anchor
#         is documented as unnecessary here.
#   P-03 (residency): the bucket location is var.region.
#   P-09: the bucket is CMEK-encrypted (the Cloud Logging service-agent binding is in kms.tf).
#   P-04 (minimise data): only redacted summaries reach this stream; the application masks
#         every turn before the audit write, and the record is immutable once here. DATA_READ
#         audit logging is enabled so every read of the evidence is itself recorded.
#
# ############################################################################ #
# # WARNING: LOCKING IS IRREVERSIBLE.                                        # #
# # The lock is variable-controlled (var.worm_locked, DEFAULT TRUE). Locking # #
# # permanently prevents reducing retention or deleting this bucket for the  # #
# # full retention window. It cannot be undone, not even with project-owner  # #
# # rights. Confirm retention_days before apply. worm_locked = true is       # #
# # REQUIRED for a compliant production deploy; set it false only for an     # #
# # evaluation stack that must stay deletable (NOT compliant).               # #
# ############################################################################ #

resource "google_logging_project_bucket_config" "worm_audit" {
  project        = var.project_id
  location       = var.region # the selected, allowlisted region (P-03)
  bucket_id      = local.worm_bucket_id
  description    = "WORM audit bucket for E1 Contact Centre AI (six-month default retention)."
  retention_days = var.retention_days

  # IRREVERSIBLE when true (the default): see the warning above.
  locked = var.worm_locked

  # CMEK on the log bucket (P-09): explicit, because it does not cascade.
  cmek_settings {
    kms_key_name = google_kms_crypto_key.contact.id
  }

  depends_on = [
    google_project_service.required,
    google_kms_crypto_key_iam_member.logging,
  ]
}

# Route the application's audit stream, and every Cloud Audit Log, into the locked bucket.
resource "google_logging_project_sink" "audit_to_worm" {
  project     = var.project_id
  name        = local.audit_sink_name
  description = "Routes the ${local.audit_log_name} log and the Cloud Audit Logs to the WORM bucket."

  destination = "logging.googleapis.com/${google_logging_project_bucket_config.worm_audit.id}"

  # The log name here must equal the logger name the managed audit adapter constructs
  # (adapters/gcp/audit.py). A filter naming a log nobody writes routes an empty stream and
  # looks exactly like a working sink. See the note in naming.tf.
  filter = <<-EOT
    logName="projects/${var.project_id}/logs/${local.audit_log_name}"
    OR logName:"cloudaudit.googleapis.com"
  EOT

  unique_writer_identity = true
}

# --------------------------------------------------------------------------- #
# Data Access audit logs. ADMIN_WRITE is on by default; DATA_READ is not, and
# without it a read of a stored contact leaves no trace. A contact centre that
# records what was said to a customer but not who later read it back is half a
# trail, and the half it is missing is the one a complaint turns on.
# --------------------------------------------------------------------------- #
resource "google_project_iam_audit_config" "data_access" {
  project = var.project_id
  service = "allServices"

  audit_log_config {
    log_type = "DATA_READ"
  }
  audit_log_config {
    log_type = "DATA_WRITE"
  }
  audit_log_config {
    log_type = "ADMIN_READ"
  }
}
