# apis.tf: enable exactly the managed services this stack depends on.
#
# Principle map (COMPLIANCE.md):
#   P-01 (managed-first, minimal surface): only the services the pinned stack actually uses
#         are enabled. Every entry below is here because a bound gcp adapter calls it; nothing
#         is speculative.
#   P-03 (residency): enabling these is the prerequisite for the regional, CMEK-protected
#         resources the sibling files create.
#
# Four of this service's managed adapters call no Google API at all: the agent-guardrail-gateway screen,
# the enterprise-knowledge-base governed-RAG retrieval, the human-review-console review router and the MCP action catalog are HTTPS
# calls to sibling services carrying an S2S bearer, so they need no API here and no IAM role in
# iam.tf. They need their URLs, which is why variables.tf refuses a served deployment without
# them.
#
# disable_on_destroy = false, so destroying this stack does not yank platform APIs out from
# under other workloads in a shared project.

locals {
  required_services = [
    # Called by a bound adapter (src/contact_centre_conversations/adapters/gcp/).
    "aiplatform.googleapis.com",    # generation.py (Gemini drafts a cited reply, decides nothing)
    "speech.googleapis.com",        # speech.py transcribe and diarize (word offsets)
    "texttospeech.googleapis.com",  # speech.py synthesize (the spoken side of a voice contact)
    "dialogflow.googleapis.com",    # channel.py (Dialogflow CX sessions, one turn stream)
    "firestore.googleapis.com",     # contact_store.py (tenant-partitioned contacts and turns)
    "logging.googleapis.com",       # audit.py (the WORM audit sink, rule R2)
    "cloudtrace.googleapis.com",    # tracer.py (spans, content off)
    "monitoring.googleapis.com",    # log-based metrics and the security alert policies
    "run.googleapis.com",           # the serving edge
    "secretmanager.googleapis.com", # the inbound and outbound service credentials
    "storage.googleapis.com",       # the contact-audio bucket the recogniser reads
    "cloudkms.googleapis.com",      # the regional CMEK key ring
    "iap.googleapis.com",           # the identity edge the one VERIFIED adapter checks against

    # Supporting services the above require.
    "accesscontextmanager.googleapis.com", # the VPC-SC perimeter (P-03)
    "compute.googleapis.com",              # the external load balancer and Cloud Armor
    "iam.googleapis.com",                  # least-privilege service accounts
    "orgpolicy.googleapis.com",            # the residency and key-hygiene constraints (P-03)
  ]
}

resource "google_project_service" "required" {
  for_each = toset(local.required_services)

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}
