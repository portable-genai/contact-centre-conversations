# storage.tf: the in-region bucket the recogniser reads contact audio from.
#
# This exists because the managed speech adapter (adapters/gcp/speech.py) hands
# `request.audio.uri` to the Speech-to-Text v2 recogniser, and that field is a Cloud Storage
# object: a voice contact therefore needs a governed place for its audio to live. This is it:
# regional, CMEK-encrypted, uniform access, public access prevented, and not force-destroyable.
#
# Principle map (COMPLIANCE.md):
#   P-03 (residency): location is var.region. A customer's recorded voice is the rawest
#         personal data this service touches, and it never leaves the region. The recogniser
#         is pinned to the same region for the same reason.
#   P-09: CMEK, with the Cloud Storage service-agent binding in kms.tf. Uniform bucket-level
#         access removes per-object ACLs entirely (org_policy.tf enforces that project-wide).
#   P-04: no model ever sees this audio. The recogniser produces turns, the domain redacts
#         them, and only redacted text is screened, stored, retrieved against or drafted from.
#
# Nothing here holds SYNTHESISED speech: the Text-to-Speech adapter returns the audio in the
# response and the process hands it straight to the caller, so there is no write path to this
# bucket at all, which is why the serving identity gets read access and nothing more.
#
# Retention and deletion of the audio itself are the adopter's schedule to set (and a contact
# centre usually has one already), so no lifecycle rule is imposed here. The audit trail of
# what was DONE with it is separate, and that one is locked (logging_worm.tf).

resource "google_storage_bucket" "audio" {
  name                        = local.audio_bucket_name
  project                     = var.project_id
  location                    = var.region # in-country audio (P-03)
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false

  versioning {
    enabled = true
  }

  encryption {
    default_kms_key_name = google_kms_crypto_key.contact.id # CMEK (P-09)
  }

  depends_on = [
    google_project_service.required,
    google_kms_crypto_key_iam_member.storage,
  ]
}

# The serving identity reads audio and never writes it: the telephony platform records the
# contact, this service recognises it. A write role here would let the audio behind a
# transcript, a disclosure verdict or an escalation be replaced after the fact.
resource "google_storage_bucket_iam_member" "app_audio_reader" {
  bucket = google_storage_bucket.audio.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.app.email}"
}
