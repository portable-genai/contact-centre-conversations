# monitoring.tf: log-based metrics and alert policies for the posture signals.
#
# Principle map (COMPLIANCE.md):
#   P-07 / P-09 (detect, do not merely record): DATA_READ logging (logging_worm.tf) records
#         reads, but recording is not detection. These metrics and policies SURFACE the events
#         that mean the posture slipped, rather than leaving the signal unread in the WORM
#         bucket for the length of the retention window.
#
# Every filter below names a field this deployment actually emits:
#   - critical_escalations_agent_assist / critical_escalations_self_service : the managed audit
#     adapter writes AuditEvent as a struct payload, so jsonPayload.decision is "escalated" or
#     "allowed" (domain/kernel.py Decision), jsonPayload.severity carries the band and
#     jsonPayload.mode carries the mode that produced the record. The escalation signal is
#     SPLIT BY MODE deliberately: the two modes are separately gated model-quality-gate releases with
#     different risk postures, and a self-service escalation means something reached a member
#     of the public that a person now has to look at, while an agent-assist one means a trained
#     agent already had it in front of them. Summing them into one number would make the second
#     hide the first.
#   - sa_key_creation : an exportable service-account key was created. Org policy should have
#     refused it (org_policy.tf), so this firing means the policy is off or was overridden.
#   - vpc_sc_denials : a VPC Service Controls violation. In dry run this is the evidence used
#     to decide whether enforcing would break a legitimate path.
#   - cmek_changes : a CMEK key destroy or update. Key material changing is a P-09 event.
#   - edge_denials : Cloud Armor denied or throttled a request at the edge.
#
# There is deliberately no guardrail-block metric, and NOT because this service has no
# guardrail: every inbound turn is screened through the agent-guardrail-gateway (rule R1). The block is
# decided and recorded THERE, and the AuditEvent this service writes carries no field naming
# it, so a filter here would have to pattern-match the prose summary. A control that parses
# prose breaks on a wording change and reads as a green light nobody earned. Alert on blocks in
# agent-guardrail-gateway, where the field exists; add a metric here in the same commit that puts the screen
# outcome on the audit record.
#
# Alert policies are always created; var.alert_notification_channels attaches the channels.

locals {
  audit_log_filter = "logName=\"projects/${var.project_id}/logs/${local.audit_log_name}\""

  security_metrics = {
    critical_escalations_agent_assist = {
      description = "Critical-severity escalation recorded by the agent-assist mode (maker-checker, P-06)"
      filter      = "${local.audit_log_filter} AND jsonPayload.decision=\"escalated\" AND jsonPayload.severity=\"critical\" AND jsonPayload.mode=\"agent_assist\""
    }
    critical_escalations_self_service = {
      description = "Critical-severity escalation recorded by the customer-facing self-service mode (maker-checker, P-06)"
      filter      = "${local.audit_log_filter} AND jsonPayload.decision=\"escalated\" AND jsonPayload.severity=\"critical\" AND jsonPayload.mode=\"self_service\""
    }
    sa_key_creation = {
      description = "Service-account key created (org policy should forbid this)"
      filter      = "protoPayload.methodName=\"google.iam.admin.v1.CreateServiceAccountKey\""
    }
    vpc_sc_denials = {
      description = "VPC Service Controls violation"
      filter      = "protoPayload.metadata.@type=\"type.googleapis.com/google.cloud.audit.VpcServiceControlAuditMetadata\""
    }
    cmek_changes = {
      description = "CMEK key destroy or update operation"
      filter      = "protoPayload.serviceName=\"cloudkms.googleapis.com\" AND (protoPayload.methodName:\"DestroyCryptoKeyVersion\" OR protoPayload.methodName:\"UpdateCryptoKey\")"
    }
    edge_denials = {
      description = "Cloud Armor denied or throttled a request at the serving edge"
      filter      = "resource.type=\"http_load_balancer\" AND jsonPayload.enforcedSecurityPolicy.outcome=\"DENY\""
    }
  }
}

resource "google_logging_metric" "security" {
  for_each = local.security_metrics

  project     = var.project_id
  name        = "${local.metric_prefix}_${each.key}"
  description = each.value.description
  filter      = each.value.filter

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"
  }

  depends_on = [google_project_service.required]
}

resource "google_monitoring_alert_policy" "security" {
  for_each = local.security_metrics

  project      = var.project_id
  display_name = "${var.name_prefix} security: ${each.key}"
  combiner     = "OR"

  conditions {
    display_name = each.value.description

    condition_threshold {
      filter          = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.security[each.key].name}\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      duration        = "0s"

      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_DELTA"
        cross_series_reducer = "REDUCE_SUM"
      }

      trigger {
        count = 1
      }
    }
  }

  notification_channels = var.alert_notification_channels

  documentation {
    content   = "Security signal '${each.key}' fired for E1 contact centre AI. Investigate the matching entries in Cloud Logging and in the WORM audit bucket (${local.worm_bucket_id})."
    mime_type = "text/markdown"
  }

  depends_on = [google_project_service.required]
}
