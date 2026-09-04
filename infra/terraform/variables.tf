# variables.tf: the only knobs. Everything else is a concrete, prefix-derived value.
#
# Principle map (COMPLIANCE.md):
#   P-03 (residency): `region` is SELECTED AT DEPLOY TIME and validated against
#         var.allowed_regions, so an unvetted region fails at `terraform plan` rather than
#         putting contact audio and transcripts out of jurisdiction. The default is
#         asia-southeast1.
#   P-07 (auditability and retention): `retention_days` is a variable because the WORM bucket
#         lock is irreversible, so the retention window has to be a deliberate decision.
#   P-06 / R8 (maker-checker): `human_review_url` is required when the serving edge is
#         enabled, because the managed review router refuses to swallow an escalation with no
#         console configured. A deploy that would ship R8 unwired fails at plan time.
#   R1 (everything through agent-guardrail-gateway): `guardrail_url` is required for the same reason. Every
#         inbound turn is redacted and then SCREENED, so an edge with no gateway configured
#         cannot serve a single turn; it fails here instead of at the first live contact.
#
# Two deploy paths are supported:
#   - QUICK EVALUATION (project-scoped, no org-level roles): project_id plus
#     enable_vpc_sc = false, enable_org_policies = false, worm_locked = false. Everything
#     stays deletable. NOT a compliant production posture.
#   - FULL SOVEREIGN (the default): org-policy guardrails, a dry-run VPC-SC perimeter, and a
#     locked WORM bucket.

variable "project_id" {
  description = "Target GCP project id (required). Single-tenant, in-region."
  type        = string
}

variable "name_prefix" {
  description = <<-EOT
    Prefix for every named resource this stack creates (key ring, buckets, service account,
    metrics, perimeter, Cloud Run service). Two reasons to change it: two instances can then
    coexist in one project, and a destroy plus redeploy does not collide with the
    indestructible KMS key ring the previous stack left behind (key rings can never be
    deleted; a fresh prefix gives a fresh ring).
  EOT
  type        = string
  default     = "contact"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,18}$", var.name_prefix))
    error_message = "name_prefix must match ^[a-z][a-z0-9-]{2,18}$ (lowercase letter first, then lowercase letters, digits or hyphens, 3 to 19 characters)."
  }
}

variable "allowed_regions" {
  description = <<-EOT
    Residency allowlist: the regions this service may be deployed to. The region is chosen at
    deploy time (var.region) and validated against this list so an operator fails fast (P-03).
    Extend it only after confirming that the whole managed stack (Vertex AI, Speech-to-Text,
    Text-to-Speech, Dialogflow CX, Firestore, Cloud KMS, Cloud Logging, Cloud Run) and the
    residency obligation for recorded conversations are both satisfied in that region. This
    list is the same allowlist the application validates its configured region against at
    settings load.
  EOT
  type        = list(string)
  default     = ["asia-southeast1"]

  validation {
    condition     = length(var.allowed_regions) > 0
    error_message = "allowed_regions must list at least one residency-approved region."
  }
}

variable "region" {
  description = <<-EOT
    Deployment region, SELECTED AT DEPLOY TIME. Defaults to asia-southeast1, the region this
    service pins its data residency to. Validated against var.allowed_regions so an unapproved
    region fails at `terraform plan` rather than moving contact audio, transcripts and the
    audit trail out of jurisdiction (P-03). The managed recogniser is pinned to the same value
    (adapters/gcp/speech.py), because a recogniser in another jurisdiction is a residency
    breach no amount of downstream masking undoes.
  EOT
  type        = string
  default     = "asia-southeast1"

  validation {
    # Cross-variable validation (Terraform >= 1.9): fails at plan time, which is setup time.
    condition     = contains(var.allowed_regions, var.region)
    error_message = "region must be one of var.allowed_regions (the residency allowlist). Add it there first if that region is approved for this workload (P-03)."
  }
}

variable "additional_resource_locations" {
  description = <<-EOT
    Extra values appended to the gcp.resourceLocations allowlist, beyond the selected region's
    own location group. The residency-critical data plane (Cloud Run, KMS, Firestore, Cloud
    Logging, Cloud Storage, Speech-to-Text) is entirely regional and needs nothing here. The
    serving edge, however, is built from location-less global objects (the IP address, URL map,
    target proxy, certificate and forwarding rule); an organisation whose policy evaluation
    covers those must list the value its policy expects for them here rather than widening the
    regional allowlist. Empty is the strict default.
  EOT
  type        = list(string)
  default     = []
}

variable "retention_days" {
  description = "WORM audit-log retention in days. Default 180 (six months). The lock is irreversible."
  type        = number
  default     = 180

  validation {
    condition     = var.retention_days >= 180
    error_message = "Compliance retention must be at least 180 days (six months) (P-07)."
  }
}

variable "existing_locked_retention_days" {
  description = "Existing locked bucket retention in days, or 0 for a new stack. A plan may never request a lower value than a bucket already locked at."
  type        = number
  default     = 0

  validation {
    condition     = var.existing_locked_retention_days == 0 || var.existing_locked_retention_days >= 180
    error_message = "existing_locked_retention_days must be 0 for a new stack, or at least 180."
  }

  validation {
    condition     = var.existing_locked_retention_days == 0 || var.retention_days >= var.existing_locked_retention_days
    error_message = "retention_days cannot be lower than the existing locked retention. An existing stack must preserve or increase its locked value."
  }
}

variable "worm_locked" {
  description = <<-EOT
    Lock the WORM audit bucket (P-07, rule R2).
    #########################################################################
    # WARNING: LOCKING IS IRREVERSIBLE. With true, the bucket and its       #
    # retention window can NEVER be reduced or deleted until every entry    #
    # ages out (180 days by default), not even with project-owner rights.   #
    #########################################################################
    true (the default) is REQUIRED for a compliant production deploy: the audit trail is
    Write-Once-Read-Many only when locked. Set false ONLY for an evaluation or demo stack that
    must stay deletable; that posture is NOT compliant.
  EOT
  type        = bool
  default     = true
}

variable "enable_org_policies" {
  description = <<-EOT
    Create the project-level Org Policy guardrails (resourceLocations residency pin, no
    service-account key creation, uniform bucket-level access). Requires the caller to hold
    roles/orgpolicy.policyAdmin on the project. Set false for a quick project-scoped
    evaluation deploy without that role; the per-resource region pins still apply, but the
    defence-in-depth layer is skipped, which is NOT compliant for production.
  EOT
  type        = bool
  default     = true
}

variable "allowed_policy_member_domains" {
  description = <<-EOT
    Customer or directory ids (for example "C0xxxxxxx") permitted by the domain-restricted
    sharing org policy (constraints/iam.allowedPolicyMemberDomains). An empty list disables
    that policy. Only applied when enable_org_policies = true.
  EOT
  type        = list(string)
  default     = []
}

variable "access_policy_id" {
  description = <<-EOT
    Existing Access Context Manager policy id (numeric, no prefix) for the organisation.
    Required when enable_vpc_sc = true; the service perimeter is created under it. Create one
    per organisation with:
      gcloud access-context-manager policies create \
        --organization=ORG_ID --title="residency-policy"
  EOT
  type        = string
  default     = ""

  validation {
    condition     = !var.enable_vpc_sc || length(var.access_policy_id) > 0
    error_message = "enable_vpc_sc = true requires access_policy_id. Supply the organisation's Access Context Manager policy id, or set enable_vpc_sc = false for a project-scoped quick deploy."
  }
}

variable "enable_vpc_sc" {
  description = "Create the VPC Service Controls perimeter around the AI and data APIs (P-01, P-03)."
  type        = bool
  default     = true
}

variable "vpc_sc_enforce" {
  description = <<-EOT
    Enforce the VPC-SC perimeter (true) or run it in DRY-RUN / audit mode (false, the
    default). Apply with false first, watch the dry-run violation logs (the monitoring.tf
    alert surfaces them), add the operator and CI identities to var.operator_members, then
    flip to true. Never enforce blind on a path nobody has watched in dry run.
  EOT
  type        = bool
  default     = false
}

variable "operator_members" {
  description = <<-EOT
    Identities (for example "user:you@example.com",
    "serviceAccount:ci@PROJECT.iam.gserviceaccount.com") allowed to reach the
    perimeter-restricted APIs from outside the perimeter, through an Access Context Manager
    access level. An empty list creates no access level.
  EOT
  type        = list(string)
  default     = []
}

variable "alert_notification_channels" {
  description = <<-EOT
    Cloud Monitoring notification channel ids for the security alert policies (critical
    escalations in each mode, service-account key creation, VPC-SC denials, CMEK changes, edge
    denials). An empty list still creates the metrics and policies, with nowhere to notify.
  EOT
  type        = list(string)
  default     = []

  validation {
    condition = !var.production_edge_enabled || (
      length(var.alert_notification_channels) > 0 &&
      alltrue([
        for channel in var.alert_notification_channels :
        can(regex("^projects/[a-z][a-z0-9-]{4,28}[a-z0-9]/notificationChannels/[0-9]+$", channel))
      ])
    )
    error_message = "production_edge_enabled requires at least one valid Cloud Monitoring notification channel: an alert nobody receives is not an alert."
  }
}

variable "create_firestore_database" {
  description = <<-EOT
    Create the Firestore (default) database that backs the contact store. The managed store
    adapter constructs firestore.Client() with no database argument, so it reads and writes
    (default); a project holds exactly one, its location and mode are fixed at creation, and it
    cannot be recreated elsewhere. Set false when the project already has a (default) database
    in var.region, and confirm that it does before you do.
  EOT
  type        = bool
  default     = true
}

# --------------------------------------------------------------------------- #
# The two separately gated modes. Both are born OFF in the application and both
# are born off here, because they are two model-quality-gate releases with different risk
# postures: agent-assist whispers to a trained human, self-service reaches a
# member of the public. Enabling one grants nothing to the other.
# --------------------------------------------------------------------------- #

variable "enable_agent_assist" {
  description = <<-EOT
    Serve the agent-assist mode (CONTACT_AGENT_ASSIST). Internal decision-support: a whisper
    copilot beside a live human agent. Born off, and set EXPLICITLY on the service either way,
    because the application reads the flag in three states and an emptied value refuses to
    boot rather than inheriting the unset default.
  EOT
  type        = bool
  default     = false
}

variable "agent_assist_promotion_bundle" {
  description = <<-EOT
    The model-quality-gate metric bundle whose promotion verdict authorises the agent-assist mode
    (CONTACT_AGENT_ASSIST_BUNDLE), for example "contact-centre-conversations-agent-assist". The
    application refuses to boot with a mode enabled and no bundle under any profile other than
    a deliberate offline local, so a served deployment requires it and this stack refuses at
    plan rather than shipping a container that will not start.
  EOT
  type        = string
  default     = ""

  validation {
    condition     = !(var.production_edge_enabled && var.enable_agent_assist) || length(var.agent_assist_promotion_bundle) > 0
    error_message = "enable_agent_assist on a served deployment requires agent_assist_promotion_bundle: a mode enabled on somebody else's evidence is not a promoted mode, and the application refuses to boot without it."
  }
}

variable "enable_self_service" {
  description = <<-EOT
    Serve the self-service mode (CONTACT_SELF_SERVICE). Customer-facing: nobody stands between
    the engine and the customer, which is why it is a separate release from agent-assist and
    why its escalations get their own alert policy (monitoring.tf). Born off, and set
    EXPLICITLY on the service either way, for the three-state reason above.
  EOT
  type        = bool
  default     = false
}

variable "self_service_promotion_bundle" {
  description = <<-EOT
    The model-quality-gate metric bundle whose promotion verdict authorises the self-service mode
    (CONTACT_SELF_SERVICE_BUNDLE), for example "contact-centre-conversations-self-service". Required
    on a served deployment for the same reason as the agent-assist bundle, and it may never be
    the same bundle: a strong result in one mode can never carry the other over the line.
  EOT
  type        = string
  default     = ""

  validation {
    condition     = !(var.production_edge_enabled && var.enable_self_service) || length(var.self_service_promotion_bundle) > 0
    error_message = "enable_self_service on a served deployment requires self_service_promotion_bundle: a customer-facing mode enabled on somebody else's evidence is not a promoted mode, and the application refuses to boot without it."
  }

  validation {
    condition     = var.self_service_promotion_bundle == "" || var.self_service_promotion_bundle != var.agent_assist_promotion_bundle
    error_message = "the two modes must name DIFFERENT promotion bundles: they promote separately, and one shared bundle would let the safer mode's evidence promote the riskier one."
  }
}

# --------------------------------------------------------------------------- #
# Runtime wiring for the serving edge. Each of these becomes an environment
# variable on the Cloud Run service, so the deployed posture is described here
# rather than assembled by hand at the console.
# --------------------------------------------------------------------------- #

variable "human_review_url" {
  description = <<-EOT
    The human-review-console the managed review router submits escalations to
    (HUMAN_REVIEW_URL). Rule R8 says an escalation is ROUTED and never merely flagged, and
    the managed router refuses rather than swallowing one when this is empty, so the serving
    edge requires it: a deploy that would ship R8 unwired fails here instead of at the first
    escalation. HTTPS is required, because the payload carries a redacted contact.
  EOT
  type        = string
  default     = ""

  validation {
    condition     = var.human_review_url == "" || can(regex("^https://", var.human_review_url))
    error_message = "human_review_url must be an https:// URL."
  }

  validation {
    condition     = !var.production_edge_enabled || can(regex("^https://", var.human_review_url))
    error_message = "production_edge_enabled requires human_review_url (rule R8): the managed review router refuses to run with no console configured."
  }
}

variable "guardrail_url" {
  description = <<-EOT
    The agent-guardrail-gateway Agent Guardrail Gateway every inbound turn is screened through after redaction
    (GUARDRAIL_GATEWAY_URL, rule R1). The serving edge requires it: screening is not optional on
    this service, only a CLEAN screen may reach retrieval or generation, and a screen that
    cannot answer is UNAVAILABLE and fails closed per mode. With this empty the managed
    adapter refuses at the first turn, so the refusal belongs at plan time instead.
  EOT
  type        = string
  default     = ""

  validation {
    condition     = var.guardrail_url == "" || can(regex("^https://", var.guardrail_url))
    error_message = "guardrail_url must be an https:// URL: the screened text is already redacted, but it is still conversation content."
  }

  validation {
    condition     = !var.production_edge_enabled || can(regex("^https://", var.guardrail_url))
    error_message = "production_edge_enabled requires guardrail_url (rule R1): every inbound turn is screened through the agent-guardrail-gateway, so a served deployment with none configured cannot handle a single turn."
  }
}

variable "retrieval_url" {
  description = <<-EOT
    The enterprise-knowledge-base governed-RAG service the managed retrieval adapter queries
    (KNOWLEDGE_BASE_URL, rule R3). The serving edge requires it: a suggested reply is
    grounded in retrieved passages or it is not produced at all, and the managed adapter
    refuses an unconfigured index rather than answering from the model's own memory.
  EOT
  type        = string
  default     = ""

  validation {
    condition     = var.retrieval_url == "" || can(regex("^https://", var.retrieval_url))
    error_message = "retrieval_url must be an https:// URL."
  }

  validation {
    condition     = !var.production_edge_enabled || can(regex("^https://", var.retrieval_url))
    error_message = "production_edge_enabled requires retrieval_url (rule R3): empty retrieval means no suggestion, and an unconfigured index is not the same thing as an empty one."
  }
}

variable "tool_catalog_url" {
  description = <<-EOT
    The client's MCP / A2A action service the self-service mode describes and executes actions
    against (CONTACT_TOOL_CATALOG_URL). Required only when self-service is served, because
    that is the mode that takes actions; agent-assist suggests and never acts. The catalog is
    also where the `consequential` flag lives, so a client can reclassify an action without a
    release here.
  EOT
  type        = string
  default     = ""

  validation {
    condition     = var.tool_catalog_url == "" || can(regex("^https://", var.tool_catalog_url))
    error_message = "tool_catalog_url must be an https:// URL."
  }

  validation {
    condition     = !(var.production_edge_enabled && var.enable_self_service) || can(regex("^https://", var.tool_catalog_url))
    error_message = "serving the self-service mode requires tool_catalog_url: with no catalog the action surface refuses at the first live contact instead of at plan time."
  }
}

variable "quality_service_url" {
  description = <<-EOT
    The model-quality-gate AI-quality service that owns the promotion verdict (CONTACT_QUALITY_URL, rule
    R5). Empty leaves the variable unset, so the application takes its documented default.
    Never set it to an empty string on the service: the eval adapter treats SET-AND-EMPTY as
    naming no promotion authority at all, and refuses.
  EOT
  type        = string
  default     = ""
}

variable "otlp_endpoint" {
  description = <<-EOT
    OpenTelemetry collector endpoint (OTEL_EXPORTER_OTLP_ENDPOINT, rule R2). Set it to the
    agent-observability collector to send spans there; leave it empty and the tracer exports straight to Cloud
    Trace. Empty means the variable is not set on the service at all.
  EOT
  type        = string
  default     = ""
}

variable "iap_audience" {
  description = <<-EOT
    The IAP-protected resource the managed identity adapter verifies every assertion against
    (CONTACT_IAP_AUDIENCE): "/projects/<PROJECT_NUMBER>/global/backendServices/<BACKEND_ID>".

    This is deliberately a variable and not a reference to the backend service created here,
    because the backend service is built FROM the Cloud Run service and would form a cycle.
    The first apply therefore leaves it empty, and the application fails closed exactly as
    documented: it starts, stays health-checkable, and refuses every end-user request with a
    503 naming this variable. Read the `iap_audience` output after that apply, set this
    variable to it, and apply again.
  EOT
  type        = string
  default     = ""

  validation {
    condition     = var.iap_audience == "" || can(regex("^/projects/[0-9]+/global/backendServices/[0-9]+$", var.iap_audience))
    error_message = "iap_audience must be empty or of the form /projects/<PROJECT_NUMBER>/global/backendServices/<BACKEND_SERVICE_ID>."
  }
}

variable "additional_secret_env" {
  description = <<-EOT
    Environment variable name to an immutable existing Secret Manager secret version, mounted
    on the API service. This is how the inbound service credential (CONTACT_S2S_TOKEN) and the
    outbound credentials (S2S_TOKEN and S2S_SIGNING_KEY for agent-guardrail-gateway, enterprise-knowledge-base and the action
    catalog; HUMAN_REVIEW_S2S_TOKEN and HUMAN_REVIEW_S2S_SIGNING_KEY for the review console) reach the
    process: no secret value is ever written into this configuration. Names this stack sets
    itself are reserved, so a secret cannot silently shadow the residency, identity, mode or
    routing wiring.
  EOT
  type = map(object({
    secret_id = string
    version   = string
  }))
  default = {}

  validation {
    condition = alltrue([
      for name, secret in var.additional_secret_env :
      can(regex("^[A-Z][A-Z0-9_]{1,127}$", name)) &&
      length(secret.secret_id) > 0 &&
      can(regex("^[1-9][0-9]*$", secret.version)) &&
      !contains([
        "CONTACT_PROFILE",
        "CONTACT_SETTINGS",
        "CONTACT_IAP_AUDIENCE",
        "CONTACT_AGENT_ASSIST",
        "CONTACT_AGENT_ASSIST_BUNDLE",
        "CONTACT_SELF_SERVICE",
        "CONTACT_SELF_SERVICE_BUNDLE",
        "CONTACT_QUALITY_URL",
        "CONTACT_TOOL_CATALOG_URL",
        "GOOGLE_CLOUD_PROJECT",
        "GCP_REGION",
        "GUARDRAIL_GATEWAY_URL",
        "HUMAN_REVIEW_URL",
        "KNOWLEDGE_BASE_URL",
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "PORT",
      ], name)
    ])
    error_message = "additional_secret_env requires uppercase non-reserved names, non-empty secret ids and numeric versions (never \"latest\": a moving version is a payload nobody reviewed)."
  }
}

# --------------------------------------------------------------------------- #
# The serving edge itself.
# --------------------------------------------------------------------------- #

variable "production_edge_enabled" {
  description = "Provision the Cloud Run service and its external load-balancer edge. Requires an immutable image and a domain."
  type        = bool
  default     = false
}

variable "api_image" {
  description = "Immutable API image reference including its @sha256 digest."
  type        = string
  default     = ""

  validation {
    condition     = !var.production_edge_enabled || can(regex("@sha256:[0-9a-f]{64}$", var.api_image))
    error_message = "production_edge_enabled requires api_image pinned by @sha256 digest: a tag can be re-pushed, so a tag pin lets what serves change with no diff."
  }
}

variable "service_domain" {
  description = "Dedicated DNS name for the production service origin."
  type        = string
  default     = ""

  validation {
    condition     = !var.production_edge_enabled || can(regex("^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])$", var.service_domain))
    error_message = "production_edge_enabled requires a valid lowercase service_domain."
  }
}

variable "dns_managed_zone" {
  description = "Optional existing Cloud DNS managed zone in which to create service_domain."
  type        = string
  default     = ""
}

variable "edge_iap_enabled" {
  description = <<-EOT
    Put Identity-Aware Proxy in front of the backend service. TRUE is the shipped posture: the
    only identity adapter this service declares VERIFIED is the IAP one, which checks the
    assertion's signature against IAP's own key set, its audience against CONTACT_IAP_AUDIENCE,
    its expiry and its issuer. With IAP off, no end user can be authenticated at all and every
    end-user route answers 401, so false is only meaningful for a service-to-service-only
    installation that fronts its own identity somewhere else.
  EOT
  type        = bool
  default     = true
}

variable "iap_members" {
  description = <<-EOT
    Principals granted roles/iap.httpsResourceAccessor on the backend service, for example
    "group:contact-centre-agents@example.com". Empty grants nobody, which is the correct
    default: IAP with no members denies everyone rather than admitting anyone.
  EOT
  type        = list(string)
  default     = []
}

variable "iap_oauth2_client_id" {
  description = "OAuth 2.0 client id for IAP. Leave empty to use the Google-managed client."
  type        = string
  default     = ""
}

variable "iap_oauth2_client_secret" {
  description = "OAuth 2.0 client secret for IAP. Leave empty to use the Google-managed client."
  type        = string
  default     = ""
  sensitive   = true
}

variable "edge_min_instances" {
  description = "Minimum instances for the API service. A live contact waits on a cold start, so the floor is one."
  type        = number
  default     = 1

  validation {
    condition     = var.edge_min_instances >= 0
    error_message = "edge_min_instances must be zero or greater."
  }
}

variable "edge_max_instances" {
  description = "Maximum instances for the API service."
  type        = number
  default     = 10

  validation {
    condition     = var.edge_max_instances >= var.edge_min_instances
    error_message = "edge_max_instances must be greater than or equal to edge_min_instances."
  }
}

variable "edge_per_source_rate_limit_per_minute" {
  description = "Cloud Armor request ceiling per source IP per minute before HTTP 429."
  type        = number
  default     = 600

  validation {
    condition     = var.edge_per_source_rate_limit_per_minute >= 10 && var.edge_per_source_rate_limit_per_minute <= 10000
    error_message = "edge_per_source_rate_limit_per_minute must be from 10 to 10000."
  }
}
