# iam.tf: the least-privilege serving identity.
#
# Principle map (COMPLIANCE.md):
#   P-09 (defence in depth, least privilege): one serving identity that holds only the roles
#         the turn pipeline needs (open a channel session, recognise and synthesise speech,
#         persist a contact and its redacted turns, write audit and traces, draft a cited
#         reply, read its own secrets). No shared kitchen-sink account and no primitive roles.
#   P-03 (residency): the identity is project-scoped and every service it reaches is regional.
#   R1 / R3 / R8: screening through Hrz1, retrieval through Hrz2, routing an escalation to the
#         Hrz7 console and describing an action through the client's catalog are all outbound
#         HTTPS calls carrying a service credential from Secret Manager, not GCP IAM roles, so
#         nothing is granted for any of them here.
#
# There is deliberately ONE service account. Doc1 carries a second identity for its Agent
# Runtime; this repo's agent surface is a set of plain tool callables that run inside the same
# process as the API (nothing in agent/ needs a runtime to import), so a second identity would
# have nothing to attach to and would only widen what is provisioned. Add one in the same
# commit that deploys the agent somewhere else, never before.

resource "google_service_account" "app" {
  account_id   = local.app_sa_id
  display_name = "E1 Contact Centre AI (serving / API)"
  project      = var.project_id

  depends_on = [google_project_service.required]
}

locals {
  # Every role below is traceable to a bound adapter. aiplatform.user covers the generation
  # model, which drafts a cited reply and never produces a step, a verdict or a band.
  #
  # Text-to-Speech has no predefined role of its own: synthesis is authorised by the API being
  # enabled (apis.tf) and the caller presenting this identity, so there is nothing to grant and
  # inventing a role name here would fail at apply.
  app_roles = [
    "roles/aiplatform.user",              # generation.py
    "roles/speech.client",                # speech.py (recognise and diarize)
    "roles/dialogflow.client",            # channel.py (detect intent on an existing CX agent)
    "roles/datastore.user",               # contact_store.py (no datastore.owner)
    "roles/logging.logWriter",            # audit.py (write only: it cannot read the WORM trail)
    "roles/cloudtrace.agent",             # tracer.py
    "roles/secretmanager.secretAccessor", # the inbound and outbound service credentials
  ]
}

resource "google_project_iam_member" "app" {
  for_each = toset(local.app_roles)
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.app.email}"
}

# The app uses the CMEK for the envelope operations it performs directly.
resource "google_kms_crypto_key_iam_member" "app" {
  crypto_key_id = google_kms_crypto_key.contact.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:${google_service_account.app.email}"
}
