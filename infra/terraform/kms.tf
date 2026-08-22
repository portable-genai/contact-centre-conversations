# kms.tf: one regional Customer-Managed Encryption Key (CMEK), in country.
#
# Principle map (COMPLIANCE.md):
#   P-09 (defence in depth): CMEK DOES NOT CASCADE. A key on one resource does not protect
#         what that resource hands to another service, so every managed service that encrypts
#         with this key gets its OWN service-agent binding below, and each resource names the
#         key in its own file. There is no project-wide grant anywhere in this stack.
#   P-03 (residency): the key ring location is var.region, a REGIONAL key ring, never the
#         global or multi-region one. Regional CMEK is what pins the crypto material in
#         country alongside the conversations it protects.
#
# NOTE: key rings are indestructible. `terraform destroy` cannot remove the ring, so a
# redeploy into the same project must either import it or use a fresh var.name_prefix
# (naming.tf derives the ring and key names from it).

resource "google_kms_key_ring" "contact" {
  name     = local.kms_ring_name
  location = var.region # regional, in-country key material (P-03)

  depends_on = [google_project_service.required]
}

resource "google_kms_crypto_key" "contact" {
  name     = local.kms_key_name
  key_ring = google_kms_key_ring.contact.id

  purpose         = "ENCRYPT_DECRYPT"
  rotation_period = "7776000s" # 90 days

  version_template {
    algorithm        = "GOOGLE_SYMMETRIC_ENCRYPTION"
    protection_level = "SOFTWARE"
  }

  lifecycle {
    # A destroyed key is unrecoverable and would strand every CMEK-encrypted contact,
    # transcript, recording and audit entry.
    prevent_destroy = true
  }
}

# --------------------------------------------------------------------------- #
# Per-service-agent key bindings. Each managed service encrypts under its OWN
# service agent, so every one of them needs its own binding here (P-09). The
# agent addresses are the documented, project-number-derived identities.
#
# Two managed services this stack talks to get NO binding, and both absences are
# deliberate:
#   - Text-to-Speech synthesises and stores nothing. The audio comes back in the
#     response and never lands in a Google-managed store, so there is no
#     at-rest surface for a key to protect.
#   - Dialogflow CX holds conversation state, but this stack does not create the
#     CX agent: the channel adapter opens a session on the client's existing
#     agent (projects/-/locations/<region>/agents/-), which may not even live in
#     this project. Bind this key on that agent in the commit that provisions
#     it; a binding here would name a service agent for a resource nobody in
#     this configuration creates.
# --------------------------------------------------------------------------- #
data "google_project" "this" {
  project_id = var.project_id
}

# Vertex AI, for the grounded reply the model drafts.
resource "google_kms_crypto_key_iam_member" "aiplatform" {
  crypto_key_id = google_kms_crypto_key.contact.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:service-${data.google_project.this.number}@gcp-sa-aiplatform.iam.gserviceaccount.com"
}

# Speech-to-Text, for the recogniser and the diarizer that turn a call into turns.
resource "google_kms_crypto_key_iam_member" "speech" {
  crypto_key_id = google_kms_crypto_key.contact.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:service-${data.google_project.this.number}@gcp-sa-speech.iam.gserviceaccount.com"
}

# Firestore, for the tenant-partitioned contact store (the evidence behind the 403).
resource "google_kms_crypto_key_iam_member" "firestore" {
  crypto_key_id = google_kms_crypto_key.contact.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:service-${data.google_project.this.number}@gcp-sa-firestore.iam.gserviceaccount.com"
}

# Cloud Storage, for the contact-audio bucket.
resource "google_kms_crypto_key_iam_member" "storage" {
  crypto_key_id = google_kms_crypto_key.contact.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:service-${data.google_project.this.number}@gs-project-accounts.iam.gserviceaccount.com"
}

# Cloud Logging, for the locked WORM audit bucket.
resource "google_kms_crypto_key_iam_member" "logging" {
  crypto_key_id = google_kms_crypto_key.contact.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:service-${data.google_project.this.number}@gcp-sa-logging.iam.gserviceaccount.com"
}

# Cloud Run, for the serving revision's own encrypted storage.
resource "google_kms_crypto_key_iam_member" "run" {
  crypto_key_id = google_kms_crypto_key.contact.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:service-${data.google_project.this.number}@serverless-robot-prod.iam.gserviceaccount.com"
}
