# firestore.tf: the tenant-partitioned contact store, regional and CMEK-encrypted.
#
# Matches the reference set because this service genuinely needs the same storage shape,
# not because the file was there: the managed ContactStorePort adapter
# (adapters/gcp/contact_store.py) is a Firestore adapter, and it puts the tenant in the
# DOCUMENT PATH (contact_centre_conversations/<tenant>/contacts/<id>) rather than in a query filter, so a
# cross-tenant read raises rather than depending on the next method remembering to filter.
# What is NOT ported is cdd-sow-research's browser-flow TTL fields, its alias, outbox, replay and
# rate-limit collections and their composite index: those belong to its embedded-grant browser
# flow, which this service does not have.
#
# Principle map (COMPLIANCE.md):
#   P-03 (residency): location_id is var.region, so a contact and its redacted turns stay in
#         country.
#   P-09: CMEK on the database, with the Firestore service-agent binding in kms.tf.
#   P-07: point-in-time recovery and delete protection, because the stored turns are the
#         evidence behind a maker-checker decision and a complaint can outlive the quarter.
#
# The database is (default), not a named one. The adapter constructs firestore.Client() with no
# database argument, so (default) is the database it reads and writes; provisioning a named
# database here would create something the application never opens. A project holds exactly one
# (default) database and its location and mode are fixed at creation, so set
# var.create_firestore_database = false when the project already has one, after confirming that
# it is in var.region. Getting this wrong is not recoverable by editing Terraform.

resource "google_firestore_database" "contacts" {
  count = var.create_firestore_database ? 1 : 0

  project     = var.project_id
  name        = "(default)"
  location_id = var.region # in-country contacts and turns (P-03)
  type        = "FIRESTORE_NATIVE"

  cmek_config {
    kms_key_name = google_kms_crypto_key.contact.id
  }

  delete_protection_state           = "DELETE_PROTECTION_ENABLED"
  point_in_time_recovery_enablement = "POINT_IN_TIME_RECOVERY_ENABLED"

  depends_on = [
    google_project_service.required,
    google_kms_crypto_key_iam_member.firestore,
  ]
}
