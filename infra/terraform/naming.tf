# naming.tf: every resource name derived from var.name_prefix, in one place.
#
# A non-default prefix lets a second instance coexist in the same project, and lets a destroy
# plus redeploy sidestep the indestructible KMS key ring a previous stack left behind.
#
# Length note: GCP service-account ids must be 6 to 30 characters. The longest derived id is
# <prefix>-app, so any prefix the variable's own regex accepts is safe here.
#
# One name is NOT derived: the audit log name. The managed audit adapter
# (src/contact_centre_conversations/adapters/gcp/audit.py) writes to the logger
# "contact_centre_conversations-audit" as a code constant, and the WORM sink filter must name the same log
# or it routes nothing. Deriving it from the prefix here would produce a filter that quietly
# matches an empty stream, which looks exactly like a working sink. Change one and change the
# other in the same commit.

locals {
  prefix   = var.name_prefix
  prefix_u = replace(var.name_prefix, "-", "_")

  # KMS (kms.tf). Key rings are indestructible: a new prefix means a new ring.
  kms_ring_name = "${var.name_prefix}-centre-ring"
  kms_key_name  = "${var.name_prefix}-centre-cmek"

  # WORM audit trail (logging_worm.tf, monitoring.tf).
  worm_bucket_id  = "${var.name_prefix}-centre-worm"
  audit_sink_name = "${var.name_prefix}-audit-to-worm"

  # Fixed by the application, not by this stack. See the header note above.
  audit_log_name = "contact_centre_conversations-audit"

  # Service account (iam.tf).
  app_sa_id = "${var.name_prefix}-app"

  # Storage (storage.tf). Project-qualified, because bucket names are globally unique.
  audio_bucket_name = "${var.project_id}-${var.name_prefix}-audio"

  # VPC-SC (vpc_sc.tf): perimeter and access-level names allow letters, digits and underscores.
  perimeter_name    = "${local.prefix_u}_perimeter"
  access_level_name = "${local.prefix_u}_operators"

  # Log-based metrics (monitoring.tf).
  metric_prefix = local.prefix_u

  # Serving edge (production_edge.tf).
  api_service_name = "${var.name_prefix}-api"
}
