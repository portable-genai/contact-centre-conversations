# Runbook: Contact Centre AI (E1)

## Deploy (gcp)
1. `CONTACT_PROFILE=gcp`, install `.[gcp]`, region `asia-southeast1`.
2. Apply `infra/terraform/`. See "The deploy stack" below for what it provisions and in what
   order; `make tf-check` validates the whole configuration offline first, with no project and
   no credentials.
3. Ingress is fronted by the external load balancer and IAP; the app authenticates the S2S
   caller fail-closed (`CONTACT_S2S_TOKEN` local, Google OIDC plus allowlist secure).
4. **Set `CONTACT_IAP_AUDIENCE`.** Without it the service starts, stays
   health-checkable and refuses every end-user request with 503 naming this variable. See below.

## The deploy stack

`infra/terraform/` is the enforcement half of this repo's compliance posture: a control that
lives only in a document is not a control. It provisions, all pinned to `asia-southeast1`:

| File | What it enforces |
|---|---|
| `variables.tf` | the residency allowlist; an out-of-allowlist region fails at plan, and the app validates the same list at settings load |
| `org_policy.tf` | `gcp.resourceLocations` pinned to the region, no service-account key creation, uniform bucket-level access |
| `kms.tf` | one regional CMEK key with a per-service-agent binding each for Vertex AI, Speech, Firestore, Storage, Logging and Cloud Run (CMEK does not cascade) |
| `logging_worm.tf` | the locked WORM audit bucket and the sink that routes this service's audit log into it |
| `monitoring.tf` | log-based metrics and alerts on critical escalations per mode, service-account key creation, VPC-SC denials, CMEK changes and edge denials |
| `vpc_sc.tf` | the service perimeter, in DRY RUN until `vpc_sc_enforce = true` |
| `firestore.tf`, `storage.tf` | the tenant-partitioned contact store and the contact-audio bucket, each regional and CMEK-encrypted |
| `iam.tf` | one least-privilege serving identity, with no exportable key |
| `production_edge.tf` | the Cloud Run service (load-balancer ingress only), Cloud Armor per-source throttle, the HTTPS load balancer and IAP |

Order of operations that is not obvious:

1. Apply with `production_edge_enabled = false` first. The residency, encryption and audit
   stack stands up on its own and can be reviewed before anything serves traffic.
2. Locking the WORM bucket is IRREVERSIBLE. Confirm `retention_days` before that first apply.
3. The perimeter starts in dry run deliberately. Watch the `vpc_sc_denials` alert for a full
   business cycle, add the operator identities to `operator_members`, and only then set
   `vpc_sc_enforce = true`. Never enforce blind on a path nobody has watched.
4. The IAP audience needs TWO applies, because the value is the id of the backend service the
   first apply creates. Apply, read the `iap_audience` output, set the `iap_audience` variable
   to it, apply again. In between, the service is health-checkable and refuses every end-user
   request with the 503 described below, which is the documented fail-closed state and not a
   gap to work around.
5. **The two modes are released one at a time, and each one on its own evidence.**
   `enable_agent_assist` and `enable_self_service` are both born off, and each requires its own
   Hrz4 promotion bundle before a served deployment will plan. They are written onto the
   service as `on` or `off` either way, because an emptied flag refuses to boot rather than
   inheriting the unset default. With both off the platform deploys and every mode route
   answers 503, which is the right first apply.

Three sibling services are required once the edge is enabled, and the plan refuses without
them: `human_review_url` (rule R8, the console an escalation is routed to), `guardrail_url`
(rule R1, the gateway every inbound turn is screened through) and `retrieval_url` (rule R3, the
governed index a suggestion is grounded in). `tool_catalog_url` is required in addition when
self-service is served, because that is the mode that takes actions. Each of those is a
refusal the application would otherwise make at the first live contact.

`make tf-check` runs `terraform init -backend=false`, `validate`, `fmt -check` and
`terraform test`. The test file uses a mocked provider, so it proves the refusals (an
out-of-region deploy, a retention below six months, a mutable image tag, an edge with no review
console, no guardrail gateway, no knowledge base or no alert channel, a mode served on no
promotion evidence, a moving secret version) without a project and without credentials. A real
`terraform plan` needs a project and is an operator step.

## The IAP audience (required for the gcp profile)
`CONTACT_IAP_AUDIENCE` is the IAP-protected resource the assertion must be
addressed to: `/projects/<PROJECT_NUMBER>/global/backendServices/<BACKEND_SERVICE_ID>` behind an
HTTPS load balancer. Read through `iap_audience` in `config/settings.yaml`, so it resolves in the
usual three states and UNSET and SET-AND-EMPTY both land on empty.

It is not optional and there is no unverified fallback, because the fallback is the vulnerability.
`google.oauth2.id_token.verify_token` documents `audience=None` as "the audience is not verified",
so an adapter that omitted it would accept ANY Google-signed OIDC ID token, from any project and
any application, and turn its `email` claim into a verified principal on this service. The adapter
therefore refuses before it reads the assertion header at all, which also means the refusal does
not depend on the SDK being importable or on the network being up.

Two operator-facing refusals, both 503 rather than 401 because no credential the caller could
present would have helped, and both naming what to fix:

| Symptom | Cause | Fix |
|---|---|---|
| 503, detail names `CONTACT_IAP_AUDIENCE` | no audience configured | set it to the protected resource above |
| 503, detail says the verifier is not installed | `google-auth` missing from the image | install `requirements-gcp.lock` (the shipped `Dockerfile` does) |

A caller-facing failure is different: a malformed, expired, wrong-audience, wrong-issuer or
wrong-key assertion answers **401 `authentication required`**, with the specific reason recorded in
the log and the exception chain rather than returned. That asymmetry is deliberate: telling an
unauthenticated caller which check failed tells them what to change next. Nothing in this path may
answer 500; `scripts/prove-exposure-matrix.sh` in the template drives each of those cases over a
real socket from a real LAN address and fails on a bare 500, and
`tests/unit/test_iap_crypto_matrix.py` runs the real verifier over locally minted assertions with
no project, no credential and no network.

## Interactive API docs
Swagger UI (`/docs`), ReDoc (`/redoc`) and the raw OpenAPI document (`/openapi.json`) are served
under the DELIBERATE offline `local` profile and nowhere else; every other posture answers 404
because the routes are not registered at all. They are a development affordance, and on a fronted
deployment they hand an uncredentialed caller the complete route inventory and every request and
response schema, for routes that same caller cannot reach. There is no variable to switch them back
on: the schema is generated from the source and available to anyone with the repository, and a
deployment that wants to publish it should serve the artifact from somewhere that is not the
authenticated service. Removing the routes rather than guarding them is what holds under `gcp`,
where the loopback guard has deliberately stood down and the process binds every interface.

## Rate limits / body caps
Enforced at the edge. `infra/terraform/production_edge.tf` provisions the Cloud Armor policy
that throttles per source IP (`edge_per_source_rate_limit_per_minute`, 600 by default) and
answers 429 above it. The ceiling is deliberately higher than a batch service's: this edge is
driven turn by turn during live conversations and a whole site usually reaches it from a
handful of egress addresses, so a ceiling sized for one caller per contact would throttle the
contact centre rather than an abuser.

## Exposure of an unauthenticated posture
An END-USER route is authenticated here when, and only when, the identity adapter the active
binding names can produce a verified principal WITHOUT trusting a header the client wrote. That is
the single question the guard below asks, and the answer comes from the adapter itself, which
declares it (`ports/identity.py`, `config.end_user_auth_kind`). The shipped answers:

| Identity binding | Declares | End-user routes |
|---|---|---|
| `local` seeded dev personas | `client-asserted` | NOT authenticated: the caller names a persona in `X-Dev-Persona` and receives its groups and tenant |
| `gcp` IAP assertion | `verified` | authenticated: the signature (against IAP's own key set), the audience (against `CONTACT_IAP_AUDIENCE`), the expiry and the issuer are all checked before any claim is read |
| `onprem` placeholder | `unimplemented` | nobody can be authenticated until the client's own IdP adapter is bound |

So a request arrives with nothing authenticating the end user in exactly three situations, and ALL
THREE are bounded by the guard below:

1. **Nobody chose a profile.** `CONTACT_PROFILE` is absent, so no end-user
   identity scheme and no service-to-service scheme has been selected. This is what a production
   deployment looks like when the variable drops out of its environment, and it is refused rather
   than relaxed: the seeded-persona adapter will not construct (401), every S2S route answers 401,
   the dev CORS allowlist and the `X-Dev-Persona` header are withdrawn, HSTS is on, and every
   route, `/healthz` included, is refused to any non-loopback peer.
2. **The `local` profile, chosen deliberately.** The seeded personas are a client-asserted
   identity, so this is bounded whatever else is configured, INCLUDING when
   `CONTACT_S2S_TOKEN` is set. Setting that secret closes the S2S
   dependency and nothing else: it authenticates a calling SERVICE and authenticates no end user,
   so it cannot make `/v1/agent-assist/turn` or `/v1/personas` authenticated and it does NOT switch the
   guard off. Were it to, a LAN peer with no credential at all would receive the full seeded
   persona list, approver included, and a real whisper panel.
3. **The `onprem` profile with the placeholder still bound.** No identity provider is wired, so
   no end user can be authenticated. `/v1/agent-assist/turn` answers 501 with the reason and the name of the
   file to read; binding a verifying adapter (below) is what lifts both the 501 and the bound.

Symmetrically, the guard STANDS DOWN when the binding declares `verified`: `gcp` serves
`/healthz` and the discovery card to any peer (a fronted deployment must stay health-checkable
and neither carries per-caller data) while `/v1/agent-assist/turn` answers 401 without an IAP assertion. The
route does the authenticating, which is the whole reason the guard may stand down.

That is also why the declaration has to be EARNED rather than asserted. It was not: the verifier
was called with no audience and no key-set URL, so any Google-signed token from any project was
accepted, and the call was unwrapped, so a caller-supplied header that was not a JWT crashed out
of the route as a bare 500. Both are closed, and the interactive docs went with them (below):
under this profile the process really does bind every interface, so anything the guard is not
covering has to be safe on its own.

To lift the bound on an on-premises deployment, bind an identity adapter that verifies your IdP's
assertion under `adapters.identity.onprem` in `config/settings.yaml` and declare
`end_user_auth = VERIFIED` on it. See [onprem-migration.md](onprem-migration.md). Nothing else
lifts it except the explicit opt-out below.

The bound is applied twice, and the outer one is on the app object rather than on one entry point:

- `add_loopback_exposure_guard` is registered at module scope in `api/app.py`, so it holds under
  `uvicorn contact_centre_conversations.api.app:app --host 0.0.0.0` (what the Dockerfile `CMD`
  and `make run-api` do) as well as under `main()`. A non-loopback peer gets 503; a WebSocket is
  closed with 1008. A request carrying `x-forwarded-for` or `forwarded` is refused whatever it
  claims, because a proxy has already overwritten the ASGI peer address.
- `resolve_bind_host` still binds loopback in `main()`, for the same three situations: the
  start-up bound and the request-time guard read one derived posture, so a process can never bind
  every interface while refusing every caller on it.

Set `CONTACT_ALLOW_INSECURE_DEMO=1` to accept the exposure deliberately.
That is the only opt-out, and it is read per request rather than baked in at import.

`scripts/prove-exposure-matrix.sh` in the template repo drives the whole matrix (profile x S2S
token x persona header) against a real socket from a real LAN address;
`tests/unit/test_serving_path_exposure.py` and `tests/unit/test_end_user_auth_posture.py` are the
in-gate halves, the second of which fails the build if the guard's posture ever reaches a service
credential again.

## Profile misconfiguration
`CONTACT_PROFILE` is read once, in `config.py`, and it has three states:

| State | What happens |
|---|---|
| unset | No choice was recorded. The SDK-free adapters bind (the alternative is importing cloud SDKs that are not installed), but every relaxation is withdrawn and the exposure guard refuses every route to any non-loopback peer. Symptom: 401 on `/v1/agent-assist/turn` naming the variable, and 503 naming the `unconfigured` posture from off-box. Fix: set the variable. |
| set to an empty value | Refused AT IMPORT (`ConfiguredEmptyError`). The process does not start. An emptied variable is an expressed intent that names no profile, so it never inherits the unset behaviour. Common cause: a config map or deployment template that renders an empty string. |
| set but unknown, including `Local`, `LOCAL`, `GCP` | Refused AT IMPORT. A typo is not a synonym, and coercing the case would turn it into a silent choice. |

In every refusing case the process fails to boot or answers 4xx/5xx, rather than serving a first
request on a posture nobody chose. The relaxations key off a derived `exposure_profile` and the
loopback bound off a derived `bind_profile`, because those two fail closed in opposite directions:
see `config.ProfileChoice`.

## Human review routing (rule R8)
Set `HRZ_HUMAN_REVIEW_URL` to the Hrz7 console (HTTPS is required off loopback) and provide
`HRZ7_S2S_TOKEN`; `HRZ7_S2S_SIGNING_KEY` optionally signs the propagated actor. These are the
OUTBOUND credentials and are deliberately distinct from this service's own inbound
`CONTACT_S2S_TOKEN`. With the URL unset, the managed router REFUSES rather
than swallowing the escalation, so a misconfiguration is a loud failure and never a silent
auto-execution. Under the local profile the escalation goes to the review-kit outbox, which is
inspectable and flushes to the console when one becomes reachable.

## Outbound credentials for the sibling services (rules R1 and R3)
The Hrz1 guardrail screen, the Hrz2 governed index and the MCP action catalog are reached over
one shared S2S transport (`adapters/gcp/_s2s.py`) and share ONE credential pair:
`HRZ_S2S_TOKEN` is the bearer, `HRZ_S2S_SIGNING_KEY` optionally signs the propagated actor.
Both are OUTBOUND, like the `HRZ7_*` pair above and unlike this service's own inbound
`CONTACT_S2S_TOKEN`, and both belong in `.env.secrets` (see `.env.secrets.example`). The URLs
they authenticate against are `HRZ_GUARDRAIL_URL`, `HRZ_KNOWLEDGE_BASE_URL` and
`CONTACT_TOOL_CATALOG_URL` in `.env`, which are not secret and are documented there.

They fail the way the R8 pair does, which is the point: both outbound pairs refuse rather than
calling a sibling unauthenticated. `hex_service_kit.s2s.client_headers` resolves both names in
three states, so an emptied credential raises inside the builder itself; `adapters/gcp/_s2s.py`
adds the one rule that is not the commons' default, passing `require_token=` for a sibling that
is not on loopback. Resolving the pair inside the builder is what keeps the states apart: a
builder that strips a value before testing it reads an emptied credential as an absent one. The
refusal happens before the socket is opened:

| Missing value | What happens |
|---|---|
| `HRZ_S2S_TOKEN` unset, sibling NOT on loopback | `ValueError` naming the variable, raised before the request leaves. Each caller turns it into a fail-closed verdict (an unavailable screen, an unreachable index). Symptom: turns refused as unavailable, with the variable named in the log and no 401 at the far end because nothing was sent. Fix: set the secret. |
| `HRZ_S2S_TOKEN` unset, sibling on loopback | Accepted, and no `Authorization` header is attached. This is the offline zero-secret posture, the same carve-out that lets a loopback base URL be plain `http`. |
| `HRZ_S2S_TOKEN` emptied (`""` or whitespace) | `ConfiguredEmptyError` naming the variable, wherever the sibling is, loopback included. An emptied variable is an expressed intent that names no credential and never inherits the unset behaviour. Common cause: a config map or deployment template that renders an empty string. |
| `HRZ_S2S_SIGNING_KEY` unset | Accepted. The signed-actor pair is omitted rather than sent unsigned, so the sibling sees a service call carrying no end-user actor. Calls still succeed; per-actor attribution at the far end is what is lost. |
| `HRZ_S2S_SIGNING_KEY` emptied | `ConfiguredEmptyError`, for the same reason as the token. Absent is a posture; blank is a mistake. |

The `HRZ7_*` pair behaves the same way, resolved through the review kit's own three-state reader
and refused at client construction for any non-loopback console, so neither outbound pair can
leave unauthenticated. `tests/unit/test_outbound_s2s_credentials.py` holds this table to the
behaviour, and `tests/unit/test_three_state_env_reads.py` fails the build if a future adapter
hands an env var name to a two-state reader without resolving it first. Its registry of such
readers is empty today, which is the goal state and not a switched-off check: the scanner is
proved against a fictional reader, so it stays able to fail with nothing registered.

On the gcp stack both names arrive as pinned Secret Manager versions through
`additional_secret_env`, never as literal values in the configuration;
`infra/terraform/terraform.tfvars.example` shows the block with all five credential names.

## Supply chain
Installs come from the committed lockfiles. After changing a dependency run `make lock` and commit
both files, then `make audit` (`pip-audit` over both locks). CI runs the same audit as a hard
failure, so a known-vulnerable dependency blocks the merge.

## Audit operations
The local WORM log supports `verify_chain()` and JSONL export/restore.

**Configure the external head anchor for any durable audit path.** Set
`CONTACT_AUDIT_ANCHOR` (read by `audit_anchor_path` in
`config/settings.yaml`) to a file on a DIFFERENT volume from
`CONTACT_AUDIT_PATH`, ideally writable by a different principal. This is
not decoration:

- the hash chain detects an edited, deleted or reordered record, because each of those breaks a
  link;
- it CANNOT detect a truncated tail, because dropping the newest rows leaves a shorter chain
  that verifies perfectly. Only the anchored head exposes that.

Leave it unset only for the ephemeral `:memory:` store the gate uses.

Operating rules:

- **The anchor is not last-write-wins.** Once the store and the anchor disagree, the service
  REFUSES to append rather than re-anchoring the store as it now stands, because one ordinary
  append would otherwise launder the divergence. Expect a hard failure on the write path, not a
  warning in a log nobody reads.
- **Re-establishing an anchor is a deliberate act.** Verify the store out of band first (against
  an exported trail held elsewhere), then call `reanchor()`. Never as a reflex to clear an alert.
- **Verify on a schedule**, not only after an incident: `verify_audit_trail` (the agent tool) and
  `HashChainedAuditLog.verify_chain()` both return the anchor cross-check, and the tool's
  `anchored` field says whether the stronger guarantee was even available.
- A managed WORM sink does not need the anchor: it provides non-rewritability itself.

## Agent surface
The A2A discovery card is served at `/.well-known/agent-card.json` and is built from the same
tool table the runtime binds, so it cannot advertise a skill the service does not implement.
Register it with the Hrz3 registry (rule R4). The tools themselves need no agent runtime to run;
only `build_function_tools()` imports one.

## Running the integration tests
`make test-integration` runs `tests/integration/`, which the offline gate deselects. Each test
SKIPS rather than fails when its configuration is absent, so an unconfigured run reports nothing
rather than a false pass. It writes an obviously fictional audit record to the configured project
and, when `HRZ_HUMAN_REVIEW_URL` is set, submits one fictional review to the live console.

## Alerts
`infra/terraform/monitoring.tf` creates a log-based metric and an alert policy for each posture
signal: a critical-severity escalation in this service's audit log (one metric PER MODE, so a
customer-facing escalation is never summed into the agent-assist ones), a service-account key
creation (org policy should have refused it, so this firing means the policy is off), a VPC-SC
violation, a CMEK key destroy or update, and a Cloud Armor denial at the edge. Attach a channel
through `alert_notification_channels`; the serving edge refuses to plan without one, because an
alert nobody receives is not an alert.

There is deliberately no guardrail-block alert here, and not because this service has no
guardrail: every inbound turn is screened through the Hrz1 gateway. The block is decided and
recorded THERE, and the `AuditEvent` this service writes carries no field naming it, so a filter
here would have to pattern-match the prose summary and would break on a wording change. Alert on
blocks in Hrz1, where the field exists, and add a metric here in the same commit that puts the
screen outcome on the audit record.
