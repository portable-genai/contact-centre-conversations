# FAQ: compliance, conduct, privacy and model risk

For second line, conduct, privacy and model risk. [`../../COMPLIANCE.md`](../../COMPLIANCE.md) is
the authoritative mapping from every catalog principle (P-01 to P-13) and platform rule (R1 to
R8) to a control and an evidence file. This page answers the questions that come before it.

## A model talking to our customers. What actually protects them?

Four things, in this order, and each is a separate control:

1. **The model decides nothing.** Every consequential decision (intent, procedure state,
   disclosure timing, the self-service gate verdict, the action, the handoff) comes from a pure
   deterministic engine over a reviewed pack. The model drafts prose.
2. **The input is redacted and then screened**, in that order, by one object every turn passes
   through (`domain/guardrails.py`), with the screen going to the `agent-guardrail-gateway`. An unreachable
   screen becomes `UNAVAILABLE` and fails closed per mode, never CLEAN.
3. **The output is validated against the retrieved passages.**
   `domain/suggestions.validate_draft` discards a draft whole if it is too long, cites a passage
   that was not retrieved, or states a number the passages did not supply. No repair, no retry.
4. **Anything consequential is held.** It sets `requires_human_review` and is routed to the `human-review-console` in the same call that produced it (rule R8), on every surface.

Underneath all four: `self_service` defaults OFF and refuses to boot without a promotion bundle,
so reaching the public is a deliberate act with an evidence trail behind it.

## Which mode carries which risk?

They are not comparable, and `COMPLIANCE.md` treats them separately for that reason.
`agent-assist` is internal decision-support with a trained human who decides; the failure mode is
a bad suggestion an agent should catch. `self-service` reaches a member of the public directly;
the failure mode is a customer acting on drafted words. Both promote through their own `model-quality-gate`
bundle with their own rubric set, so the evidence for one is never quoted for the other.

## Is a disclosure reminder the same thing as a disclosure?

No, and the distinction matters. This service reminds the agent that a disclosure is due within
its configured window (`domain/disclosure_engine.py`); the agent says it. Whether it was actually
said, in order and in time, is measured after the contact by the sibling QA scorecard (E3), which
reads THIS repo's `kind: disclosure` pack and grades against it. The pack is the shared artifact,
so a wording is authored once and the live half and the review half cannot disagree.

## Does the model see customer personal data?

Every inbound turn is masked with the `pii-kit` jurisdiction rows before screening, before
retrieval and before generation. Agent tool results are masked again on the way out, because a
tool result becomes a model's context and an API response does not.
[`../model-card.md`](../model-card.md) states the boundary in full, including what the
speech-to-text path does with audio before any of this begins.

## Where does the data live, and is residency enforced or merely described?

Enforced. The region is chosen once (`asia-southeast1`), carried by `config/settings.yaml`,
reported by `/healthz`, printed on the agent card and pinned onto the managed recogniser, and
then held at deploy time: `infra/terraform/variables.tf` validates the effective region against
the residency allowlist at plan time, `org_policy.tf` pins `constraints/gcp.resourceLocations` to
that region's location group, and every regional resource is created in it: the CMEK key ring,
the WORM log bucket, Firestore, the contact-audio bucket and the Cloud Run service.

`infra/terraform/production_edge.tftest.hcl` is the standing gate:
`reject_region_outside_the_residency_allowlist` fails if the allowlist stops refusing, and
`residency_defaults_are_in_country` fails if any of those resources drifts off region. It runs
against a mocked provider, so `make tf-check` proves it with no project and no credentials.

Pinning the recogniser is not decoration. A recogniser in another jurisdiction receives the raw
audio, which is a residency breach no amount of downstream masking undoes.

## Is the audit trail admissible?

It is append-only and hash-chained, and the chain head is anchored to a file on a separate
volume, so an edit, a deletion, a reorder AND a truncated tail are all detectable.
`tests/unit/test_audit_anchor.py` proves each, including the control case that fails without the
anchor. The audit actor is the verified principal, never a field in the request body, and
personal data is masked before the write. Under `gcp` the trail is routed to a locked Cloud
Logging bucket at a six-month retention floor the Terraform test refuses to lower.

## What about recording and monitoring the agents themselves?

Worth naming explicitly, because it is a real consequence of an assist panel: the service
observes a live contact, which in several jurisdictions carries notice, consultation or works
council obligations toward the agent as well as the customer. That is a conduct and
employment-law question for the adopter, not a control this repo can ship.

## Which controls are NOT covered, and who owns them?

`COMPLIANCE.md` marks these honestly rather than claiming them.

- **R2, the shared observability sink.** The immutable audit half is local and tamper-evident;
  binding an observability client to `agent-observability` is open.
- **R4 and R5, the registry and the promotion gate.** The A2A card and the `--mode gate` client
  half both exist; registering with `agent-registry`, and both bundles with `model-quality-gate`, is a deployment act.
- **P-09, network perimeter.** CMEK with a per-service-agent binding, least-privilege IAM, no
  service-account keys, IAP in front of the backend and a Cloud Armor throttle all ship in
  `infra/terraform/`. Private endpoints and a distinct agent identity are recorded as open.
- **R6, intake validation.** Record the `architecture-validator` reference when the project passes it.

One row is worth reading with care rather than at face value: R3 in `COMPLIANCE.md` still says no
retrieval happens, which no longer matches the tree now that `ports/retrieval.py` is bound to
`enterprise-knowledge-base`. Treat the code as the evidence and expect that row to be corrected.

## Who owns the regulator crosswalk?

You do. `COMPLIANCE.md` maps to the catalog's own principles and rules. The mapping from those to
a MAS TRM, CPS 234, CPS 230, HKMA or PDPA control id, and the judgement that a control is
SUFFICIENT for that regulation, depends on your risk appetite, your regulator and your existing
control library. No row in that file should be quoted as regulatory assurance.

## What model-risk evidence exists today?

Nine metrics across two rubric sets run in every gate, and each is proved able to fail:
`tests/unit/test_eval_falsification.py` and `tests/unit/test_not_falsely_green.py` plant a mutant
per metric and fail the build if it still passes.

What does not exist yet is a managed-profile evaluation: the offline rubrics score the
deterministic engines and the passage-composing drafter, not live Gemini output. Registering both
bundles with `model-quality-gate` and running a managed evaluation per mode is the open item in
[`../model-card.md`](../model-card.md), and until it is done the managed model path is not
production-cleared for either mode.

One more honest caveat, from [`../practices-audit.md`](../practices-audit.md) itself: this
repository has not entered the cross-repo practices-audit matrix, so every verdict in that file
is a self-assessment against the tree rather than an independent review.
