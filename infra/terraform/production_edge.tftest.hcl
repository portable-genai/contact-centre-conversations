# production_edge.tftest.hcl: the posture claims, as executable tests.
#
# Matches the reference stack, adapted for this repo. Every run below uses `mock_provider`, so the
# whole file runs with NO credentials, NO project and NO network beyond the provider download:
#   terraform init -backend=false && terraform test
# The Doc1 runs that were NOT portable are the Mode 5 signing-key stages and the installation
# manifest contract; those are its embedded-grant browser flow, which this service does not
# have, and their module does not exist here.
#
# What these runs are for: a residency or fail-closed claim that only lives in a comment is a
# claim nobody checks. Each `expect_failures` run proves that a specific misconfiguration is
# refused at plan time rather than reaching an apply.

mock_provider "google" {}

run "residency_defaults_are_in_country" {
  command = plan

  variables {
    project_id    = "fictional-contact-sg"
    enable_vpc_sc = false
  }

  assert {
    condition     = var.region == "asia-southeast1" && var.allowed_regions == tolist(["asia-southeast1"])
    error_message = "The default region and residency allowlist must both stay asia-southeast1."
  }

  assert {
    condition     = google_kms_key_ring.contact.location == var.region
    error_message = "CMEK key material must be regional and in the deployment region, never a multi-region ring."
  }

  assert {
    condition     = google_logging_project_bucket_config.worm_audit.location == var.region
    error_message = "The WORM audit bucket must be created in the deployment region."
  }

  assert {
    condition     = google_storage_bucket.audio.location == var.region
    error_message = "Contact audio must be created in the deployment region: a recogniser or a bucket in another jurisdiction is a residency breach no downstream masking undoes."
  }

  assert {
    condition     = one(google_firestore_database.contacts[*].location_id) == var.region
    error_message = "The contact store must be created in the deployment region."
  }

  assert {
    condition     = one(google_org_policy_policy.resource_locations[*].spec[0].rules[0].values[0].allowed_values) == tolist(["in:asia-southeast1-locations"])
    error_message = "The org-policy location allowlist must pin exactly the deployment region's location group."
  }

  assert {
    condition     = one(google_org_policy_policy.disable_sa_keys[*].spec[0].rules[0].enforce) == "TRUE"
    error_message = "Service-account key creation must stay forbidden: an exported key is a credential that leaves the perimeter in a file."
  }

  assert {
    condition     = var.worm_locked && var.retention_days == 180
    error_message = "The audit bucket must stay locked at the six-month retention floor by default."
  }

  assert {
    condition     = google_logging_project_bucket_config.worm_audit.locked
    error_message = "The WORM lock must be applied to the bucket, not merely defaulted in a variable."
  }
}

run "the_audit_sink_names_the_log_the_application_writes" {
  command = plan

  variables {
    project_id    = "fictional-contact-sg"
    enable_vpc_sc = false
  }

  # adapters/gcp/audit.py names the logger as a code constant. A sink filter derived from
  # name_prefix instead would route an empty stream and look exactly like a working sink, so
  # this is asserted rather than left to a comment.
  assert {
    condition     = strcontains(google_logging_project_sink.audit_to_worm.filter, "logs/contact_centre_conversations-audit")
    error_message = "The WORM sink filter must name the log the managed audit adapter actually writes to (contact_centre_conversations-audit), or it routes nothing."
  }

  assert {
    condition     = strcontains(google_logging_metric.security["critical_escalations_self_service"].filter, "jsonPayload.mode=\"self_service\"")
    error_message = "The escalation metrics must stay split by mode: the customer-facing mode's escalations must not be summed into the agent-assist ones."
  }
}

run "perimeter_starts_in_dry_run" {
  command = plan

  variables {
    project_id       = "fictional-contact-sg"
    access_policy_id = "123456789012"
  }

  assert {
    condition     = google_access_context_manager_service_perimeter.contact[0].use_explicit_dry_run_spec
    error_message = "The perimeter must start in dry run: never enforce blind on a path nobody has watched."
  }

  assert {
    condition     = length(google_access_context_manager_service_perimeter.contact[0].status[0].restricted_services) == 0
    error_message = "In dry run the enforced status must stay open; the restricted services belong in the dry-run spec."
  }

  assert {
    condition     = contains(google_access_context_manager_service_perimeter.contact[0].spec[0].restricted_services, "speech.googleapis.com")
    error_message = "The dry-run spec must audit the speech API: recognised audio is the rawest conversation data crossing the boundary."
  }

  assert {
    condition     = contains(google_access_context_manager_service_perimeter.contact[0].spec[0].restricted_services, "dialogflow.googleapis.com")
    error_message = "The dry-run spec must audit the channel API: it carries the live turns of a contact."
  }
}

run "default_omits_the_serving_edge" {
  command = plan

  variables {
    project_id    = "fictional-contact-sg"
    enable_vpc_sc = false
  }

  assert {
    condition     = length(google_cloud_run_v2_service.api) == 0 && length(google_compute_global_forwarding_rule.edge) == 0
    error_message = "The serving edge must be opt-in, so the residency and audit stack can be applied and reviewed before anything serves."
  }
}

run "both_modes_are_born_off_on_a_served_deployment" {
  command = plan

  variables {
    project_id                  = "fictional-contact-sg"
    enable_vpc_sc               = false
    production_edge_enabled     = true
    api_image                   = "asia-southeast1-docker.pkg.dev/fictional-contact-sg/contact/api@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    service_domain              = "contact-centre.fictional-bank.example"
    human_review_url            = "https://review.fictional-bank.example"
    guardrail_url               = "https://guardrail.fictional-bank.example"
    retrieval_url               = "https://knowledge.fictional-bank.example"
    alert_notification_channels = ["projects/fictional-contact-sg/notificationChannels/123"]
  }

  assert {
    condition = alltrue([
      one([for item in google_cloud_run_v2_service.api[0].template[0].containers[0].env : item.value if item.name == "CONTACT_AGENT_ASSIST"]) == "off",
      one([for item in google_cloud_run_v2_service.api[0].template[0].containers[0].env : item.value if item.name == "CONTACT_SELF_SERVICE"]) == "off",
    ])
    error_message = "Both modes must be born OFF and said so explicitly on the service: a deployment that configured nothing serves neither, and an emptied flag refuses to boot rather than inheriting that."
  }

  assert {
    condition     = length([for item in google_cloud_run_v2_service.api[0].template[0].containers[0].env : item.name if endswith(item.name, "_BUNDLE")]) == 0
    error_message = "A disabled mode must carry no promotion bundle on the service: an empty bundle is a mode standing on no evidence and must be ABSENT, never set to empty."
  }
}

run "serving_edge_contract" {
  command = plan

  # A mocked provider leaves every computed attribute unknown at plan time, and the CMEK key
  # id is one. Overriding it during the plan is what lets the run assert that the revision is
  # bound to THAT key rather than to nothing; the value here is a stand-in for a real key id
  # and is never applied anywhere.
  override_resource {
    target          = google_kms_crypto_key.contact
    override_during = plan
    values = {
      id = "projects/fictional-contact-sg/locations/asia-southeast1/keyRings/contact-centre-ring/cryptoKeys/contact-centre-cmek"
    }
  }

  variables {
    project_id                    = "fictional-contact-sg"
    enable_vpc_sc                 = false
    production_edge_enabled       = true
    api_image                     = "asia-southeast1-docker.pkg.dev/fictional-contact-sg/contact/api@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    service_domain                = "contact-centre.fictional-bank.example"
    human_review_url              = "https://review.fictional-bank.example"
    guardrail_url                 = "https://guardrail.fictional-bank.example"
    retrieval_url                 = "https://knowledge.fictional-bank.example"
    tool_catalog_url              = "https://actions.fictional-bank.example"
    alert_notification_channels   = ["projects/fictional-contact-sg/notificationChannels/123"]
    iap_members                   = ["group:contact-centre-agents@example.com"]
    iap_audience                  = "/projects/123456789012/global/backendServices/1234567890123456789"
    enable_agent_assist           = true
    agent_assist_promotion_bundle = "contact-centre-conversations-agent-assist"
    enable_self_service           = true
    self_service_promotion_bundle = "contact-centre-conversations-self-service"
  }

  assert {
    condition     = google_cloud_run_v2_service.api[0].ingress == "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"
    error_message = "The API must reject direct public Cloud Run ingress; the load balancer is the only way in."
  }

  assert {
    condition     = google_cloud_run_v2_service.api[0].location == var.region
    error_message = "The serving revision must run in the deployment region."
  }

  assert {
    condition     = endswith(google_cloud_run_v2_service.api[0].template[0].containers[0].image, "@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    error_message = "The API image must remain the reviewed digest."
  }

  assert {
    condition     = google_cloud_run_v2_service.api[0].template[0].encryption_key == google_kms_crypto_key.contact.id
    error_message = "The revision must be bound to the regional CMEK: encryption does not cascade."
  }

  assert {
    condition     = one([for item in google_cloud_run_v2_service.api[0].template[0].containers[0].env : item.value if item.name == "CONTACT_PROFILE"]) == "gcp"
    error_message = "The cloud profile must be named explicitly on the service: an unset profile is not a usable production posture."
  }

  assert {
    condition     = one([for item in google_cloud_run_v2_service.api[0].template[0].containers[0].env : item.value if item.name == "GCP_REGION"]) == var.region
    error_message = "The application region must equal the Terraform deployment region."
  }

  assert {
    condition     = one([for item in google_cloud_run_v2_service.api[0].template[0].containers[0].env : item.value if item.name == "HUMAN_REVIEW_URL"]) == var.human_review_url
    error_message = "Rule R8: the service must be told where an escalation is routed, or the managed router refuses."
  }

  assert {
    condition     = one([for item in google_cloud_run_v2_service.api[0].template[0].containers[0].env : item.value if item.name == "GUARDRAIL_GATEWAY_URL"]) == var.guardrail_url
    error_message = "Rule R1: the service must be told which gateway screens every inbound turn, or it cannot handle one."
  }

  assert {
    condition     = one([for item in google_cloud_run_v2_service.api[0].template[0].containers[0].env : item.value if item.name == "KNOWLEDGE_BASE_URL"]) == var.retrieval_url
    error_message = "Rule R3: the service must be told which governed index grounds a suggestion."
  }

  assert {
    condition = alltrue([
      one([for item in google_cloud_run_v2_service.api[0].template[0].containers[0].env : item.value if item.name == "CONTACT_AGENT_ASSIST"]) == "on",
      one([for item in google_cloud_run_v2_service.api[0].template[0].containers[0].env : item.value if item.name == "CONTACT_SELF_SERVICE"]) == "on",
    ])
    error_message = "An enabled mode must be named on the service in a token the application accepts."
  }

  assert {
    condition = one([for item in google_cloud_run_v2_service.api[0].template[0].containers[0].env : item.value if item.name == "CONTACT_SELF_SERVICE_BUNDLE"]) != one(
      [for item in google_cloud_run_v2_service.api[0].template[0].containers[0].env : item.value if item.name == "CONTACT_AGENT_ASSIST_BUNDLE"]
    )
    error_message = "Each mode must carry its OWN Hrz4 promotion bundle: one shared bundle would let the safer mode's evidence promote the customer-facing one."
  }

  assert {
    condition     = one([for item in google_cloud_run_v2_service.api[0].template[0].containers[0].env : item.value if item.name == "CONTACT_IAP_AUDIENCE"]) == var.iap_audience
    error_message = "The verified identity adapter must receive the exact audience it checks assertions against."
  }

  assert {
    condition     = length([for item in google_cloud_run_v2_service.api[0].template[0].containers[0].env : item.name if item.name == "CONTACT_QUALITY_URL"]) == 0
    error_message = "An unset quality URL must be ABSENT from the service, never set to empty: this service reads its environment in three states and an emptied value refuses."
  }

  assert {
    condition     = length(google_compute_backend_service.api[0].iap) == 1 && google_compute_backend_service.api[0].iap[0].enabled
    error_message = "The backend service must carry IAP: it is the only mechanism by which a caller can be authenticated here."
  }

  assert {
    condition     = length(google_iap_web_backend_service_iam_member.callers) == 1
    error_message = "The named callers must be granted IAP access, or the edge admits nobody."
  }

  assert {
    condition     = length(google_compute_security_policy.api_per_source) == 1
    error_message = "The edge must provision its per-source Cloud Armor abuse boundary."
  }

  assert {
    condition     = one([for rule in google_compute_security_policy.api_per_source[0].rule : rule.rate_limit_options[0].rate_limit_threshold[0].count if rule.action == "throttle"]) == 600
    error_message = "The per-source throttle must retain the reviewed requests-per-minute ceiling."
  }

  assert {
    condition     = google_compute_global_forwarding_rule.edge[0].port_range == "443"
    error_message = "The edge must listen on 443 only: there is no plaintext listener to redirect from."
  }
}

run "reject_region_outside_the_residency_allowlist" {
  command = plan

  variables {
    project_id    = "fictional-contact-sg"
    enable_vpc_sc = false
    region        = "us-central1"
  }

  expect_failures = [var.region]
}

run "reject_retention_below_six_months" {
  command = plan

  variables {
    project_id     = "fictional-contact-sg"
    enable_vpc_sc  = false
    retention_days = 179
  }

  expect_failures = [var.retention_days]
}

run "reject_reducing_existing_locked_retention" {
  command = plan

  variables {
    project_id                     = "fictional-contact-sg"
    enable_vpc_sc                  = false
    retention_days                 = 180
    existing_locked_retention_days = 2557
  }

  expect_failures = [var.existing_locked_retention_days]
}

run "reject_perimeter_without_an_access_policy" {
  command = plan

  variables {
    project_id       = "fictional-contact-sg"
    enable_vpc_sc    = true
    access_policy_id = ""
  }

  expect_failures = [var.access_policy_id]
}

run "reject_mutable_api_image" {
  command = plan

  variables {
    project_id                  = "fictional-contact-sg"
    enable_vpc_sc               = false
    production_edge_enabled     = true
    api_image                   = "asia-southeast1-docker.pkg.dev/fictional-contact-sg/contact/api:latest"
    service_domain              = "contact-centre.fictional-bank.example"
    human_review_url            = "https://review.fictional-bank.example"
    guardrail_url               = "https://guardrail.fictional-bank.example"
    retrieval_url               = "https://knowledge.fictional-bank.example"
    alert_notification_channels = ["projects/fictional-contact-sg/notificationChannels/123"]
  }

  expect_failures = [var.api_image]
}

run "reject_edge_with_no_review_console" {
  command = plan

  variables {
    project_id                  = "fictional-contact-sg"
    enable_vpc_sc               = false
    production_edge_enabled     = true
    api_image                   = "asia-southeast1-docker.pkg.dev/fictional-contact-sg/contact/api@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    service_domain              = "contact-centre.fictional-bank.example"
    human_review_url            = ""
    guardrail_url               = "https://guardrail.fictional-bank.example"
    retrieval_url               = "https://knowledge.fictional-bank.example"
    alert_notification_channels = ["projects/fictional-contact-sg/notificationChannels/123"]
  }

  expect_failures = [var.human_review_url]
}

run "reject_edge_with_no_guardrail_gateway" {
  command = plan

  variables {
    project_id                  = "fictional-contact-sg"
    enable_vpc_sc               = false
    production_edge_enabled     = true
    api_image                   = "asia-southeast1-docker.pkg.dev/fictional-contact-sg/contact/api@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    service_domain              = "contact-centre.fictional-bank.example"
    human_review_url            = "https://review.fictional-bank.example"
    guardrail_url               = ""
    retrieval_url               = "https://knowledge.fictional-bank.example"
    alert_notification_channels = ["projects/fictional-contact-sg/notificationChannels/123"]
  }

  expect_failures = [var.guardrail_url]
}

run "reject_edge_with_no_knowledge_base" {
  command = plan

  variables {
    project_id                  = "fictional-contact-sg"
    enable_vpc_sc               = false
    production_edge_enabled     = true
    api_image                   = "asia-southeast1-docker.pkg.dev/fictional-contact-sg/contact/api@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    service_domain              = "contact-centre.fictional-bank.example"
    human_review_url            = "https://review.fictional-bank.example"
    guardrail_url               = "https://guardrail.fictional-bank.example"
    retrieval_url               = ""
    alert_notification_channels = ["projects/fictional-contact-sg/notificationChannels/123"]
  }

  expect_failures = [var.retrieval_url]
}

run "reject_a_mode_served_on_no_promotion_evidence" {
  command = plan

  variables {
    project_id                    = "fictional-contact-sg"
    enable_vpc_sc                 = false
    production_edge_enabled       = true
    api_image                     = "asia-southeast1-docker.pkg.dev/fictional-contact-sg/contact/api@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    service_domain                = "contact-centre.fictional-bank.example"
    human_review_url              = "https://review.fictional-bank.example"
    guardrail_url                 = "https://guardrail.fictional-bank.example"
    retrieval_url                 = "https://knowledge.fictional-bank.example"
    tool_catalog_url              = "https://actions.fictional-bank.example"
    alert_notification_channels   = ["projects/fictional-contact-sg/notificationChannels/123"]
    enable_self_service           = true
    self_service_promotion_bundle = ""
  }

  expect_failures = [var.self_service_promotion_bundle]
}

run "reject_self_service_with_no_action_catalog" {
  command = plan

  variables {
    project_id                    = "fictional-contact-sg"
    enable_vpc_sc                 = false
    production_edge_enabled       = true
    api_image                     = "asia-southeast1-docker.pkg.dev/fictional-contact-sg/contact/api@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    service_domain                = "contact-centre.fictional-bank.example"
    human_review_url              = "https://review.fictional-bank.example"
    guardrail_url                 = "https://guardrail.fictional-bank.example"
    retrieval_url                 = "https://knowledge.fictional-bank.example"
    tool_catalog_url              = ""
    alert_notification_channels   = ["projects/fictional-contact-sg/notificationChannels/123"]
    enable_self_service           = true
    self_service_promotion_bundle = "contact-centre-conversations-self-service"
  }

  expect_failures = [var.tool_catalog_url]
}

run "reject_edge_with_no_alert_channel" {
  command = plan

  variables {
    project_id                  = "fictional-contact-sg"
    enable_vpc_sc               = false
    production_edge_enabled     = true
    api_image                   = "asia-southeast1-docker.pkg.dev/fictional-contact-sg/contact/api@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    service_domain              = "contact-centre.fictional-bank.example"
    human_review_url            = "https://review.fictional-bank.example"
    guardrail_url               = "https://guardrail.fictional-bank.example"
    retrieval_url               = "https://knowledge.fictional-bank.example"
    alert_notification_channels = []
  }

  expect_failures = [var.alert_notification_channels]
}

run "reject_audience_without_iap" {
  command = plan

  variables {
    project_id       = "fictional-contact-sg"
    enable_vpc_sc    = false
    edge_iap_enabled = false
    iap_audience     = "/projects/123456789012/global/backendServices/1234567890123456789"
  }

  expect_failures = [terraform_data.edge_contract]
}

run "reject_reserved_secret_override" {
  command = plan

  variables {
    project_id    = "fictional-contact-sg"
    enable_vpc_sc = false
    additional_secret_env = {
      GUARDRAIL_GATEWAY_URL = {
        secret_id = "wrong-gateway"
        version   = "1"
      }
    }
  }

  expect_failures = [var.additional_secret_env]
}

run "reject_moving_secret_version" {
  command = plan

  variables {
    project_id    = "fictional-contact-sg"
    enable_vpc_sc = false
    additional_secret_env = {
      HUMAN_REVIEW_S2S_TOKEN = {
        secret_id = "hrz7-outbound-s2s"
        version   = "latest"
      }
    }
  }

  expect_failures = [var.additional_secret_env]
}
