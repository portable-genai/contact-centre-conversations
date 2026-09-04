# FAQ: security review

For AppSec and second-line security architecture. Everything below names the file that is the
evidence, so a reviewer can read the control rather than the claim.

## Who does the service think the caller is, and can the caller influence that?

Identity is resolved server-side and nothing the client writes contributes to it. Under `gcp`,
`adapters/gcp/identity.py` verifies the Cloud IAP-injected assertion with an explicit
`audience=` (the configured `CONTACT_IAP_AUDIENCE`) and IAP's own `certs_url=`, and checks the
issuer itself because `verify_token` does not. That audience is read in three states: unset or
deliberately emptied both REFUSE, because `audience=None` means the audience is not verified and
would accept any Google-signed token from any project. Caller faults answer 401 with the reason
kept in the log; deployment faults (no audience, no verifier installed) answer 503 naming the
fix, so a misconfiguration never reads as a rejected user.

`tests/unit/test_iap_identity.py` runs in every `make gate`, and
`tests/unit/test_iap_crypto_matrix.py` runs the real verifier over locally minted assertions in
its own CI job, which fails if that test skips.

## What stops an unauthenticated peer reaching the service in the offline profile?

`add_loopback_exposure_guard` is bound at MODULE scope in `api/app.py`, because the Dockerfile
`CMD` and `make run-api` serve the app object and a bound that only exists inside `main()` never
runs in a shipped process. `tests/unit/test_serving_path_exposure.py` is the standing gate.

The guard's posture is derived from the IDENTITY BINDING and from nothing else: a route is
authenticated when the bound adapter can produce a verified principal without trusting a header
the client wrote, and the adapter declares that (`ports/identity.py`: `VERIFIED` /
`CLIENT_ASSERTED` / `UNIMPLEMENTED`). `CONTACT_S2S_TOKEN` may never enter that decision. It
authenticates a calling SERVICE and no end user, and setting it closes the S2S routes without
opening anything else. `tests/unit/test_end_user_auth_posture.py` walks the guard's argument
through the constants it names and fails the build if a credential reappears at any depth.

Interactive docs (`/docs`, `/redoc`, `/openapi.json`) are ABSENT rather than guarded under `gcp`,
because a guard the profile has switched off is no guard.

## This service reaches the public. What screens the input?

The order is fixed and one object owns it: redact, then screen, then retrieve, then generate.
`domain/guardrails.py` masks every inbound turn with the `pii-kit` jurisdiction rows and then
screens it through `ports/guardrail.py`, a thin S2S client to the `agent-guardrail-gateway`. Only a CLEAN
screen may reach retrieval or generation.

The important property is what happens when screening is impossible. An adapter that cannot
reach the gateway RAISES, and `TurnGuard` converts the raise into `ScreenOutcome.UNAVAILABLE`,
which fails closed per mode through `degradation_for`. What no adapter may do is return CLEAN
when it did not screen: that one behaviour would make the whole control decorative.
`tests/unit/test_turn_guardrails.py` is the standing gate, and `guardrail_url` unconfigured means
the managed adapter REFUSES rather than defaulting to localhost.

## What reaches a model?

A redacted turn and a passage list that retrieval already returned and the guardrail already
screened. That is the entire input surface: `ports/generation.py` offers no free-form completion,
no tool-calling loop and no conversation-history parameter, so a caller cannot ask the model a
question the knowledge base did not already answer.

What comes back is untrusted regardless of the response schema.
`domain/suggestions.validate_draft` discards a draft whole, never repairs it, if it is over
`MAX_SUGGESTION_CHARS`, cites a passage that was not retrieved, or contains a number the passages
did not supply. Agent tool results are masked again on the way out, because a tool result becomes
a model's context and an API response does not. See [`../model-card.md`](../model-card.md).

## Can a mode be switched on by accident?

No. Both mode flags default off and `domain/modes.py` resolves each in three states: unset is
off, deliberately EMPTIED refuses to boot rather than inheriting that default, and an
unrecognised or mis-capitalised value refuses too. A mode enabled with no `model-quality-gate` promotion bundle
refuses to boot under any profile except a deliberate offline `local`.
`tests/unit/test_mode_gating.py` is the gate. With both modes off, every mode route refuses.

## Can the assistant do anything to an account?

Only what the action catalog allows, and never without the deterministic gate.
`domain/policy_gate.py` produces the verdict, `domain/action_engine.py` decides the action, and
anything consequential sets `requires_human_review` and is ROUTED to the `human-review-console` in the same
call that produced it (`tests/unit/test_maker_checker.py`,
`tests/unit/test_review_routing.py`). The self-service intent list is an ALLOWLIST: an intent
nobody configured is refused rather than attempted, and a missing packs directory yields the
empty library, which refuses everything.

## Is there PII in this repository?

Only synthetic. Every pack, corpus passage, scripted stream, contact id and eval case uses
obviously fictional parties and `.example` domains.

## How are secrets handled?

No secret value is committed. `config/settings.yaml` and `.env.example` carry names and
non-secret defaults; `.env.secrets.example` carries the NAMES with placeholder values, and
`tests/unit/test_repo_artifacts.py` fails the build if a real-looking value appears in either.
Inbound and outbound credentials are deliberately distinct variables: this service's own
`CONTACT_S2S_TOKEN` is not the `HUMAN_REVIEW_S2S_TOKEN` it presents to the review console.
`tests/unit/test_outbound_s2s_credentials.py` keeps the two apart.

Every security-relevant environment read resolves three states. Unset, set-and-empty and
set-and-valid are different, and a value an operator deliberately emptied never inherits the more
permissive unset default. `tests/unit/test_three_state_env_reads.py` walks the AST of `src/`,
`scripts/` and `eval/`, `tests/unit/test_delegated_env_reads.py` covers the delegated cases, and
`ui/tests/three-state-env-reads.test.mjs` does the same for every shipped `.mjs`, `.ts` and
`.tsx` in the micro-frontend.

## What is the supply-chain posture?

Both lockfiles are committed and installed with `--no-deps` by `make install`, by CI and by the
Dockerfile, with the catalog commons pinned to 40-character COMMIT shas rather than tags, because
a tag can be moved and a moved tag changes what installs with no diff. The base image is
digest-pinned, Actions are SHA-pinned, dependabot covers every ecosystem the repo has, and
`pip-audit` is a hard CI failure. `tests/unit/test_repo_artifacts.py` asserts each of those from
inside the repo, including asking git whether each pinned sha is a commit object rather than an
annotated tag object.

## What does the browser boundary look like?

The browser never asserts who it is. In `ui/`, every client-supplied actor, tenant, role, ACL and
authorization header is discarded before forwarding (`ui/lib/embed-policy.mjs`), identity is
resolved server-side (`ui/lib/identity-policy.mjs`), and the service credential is read from the
server environment so it never reaches a bundle. Framing and CORS are per-tenant allowlists that
refuse a wildcard, and an unset tenant allowlist denies.

## What is explicitly NOT in scope here?

- **The screening engine itself.** `agent-guardrail-gateway` owns detection. This repo owns the ORDER, the fail-closed
  conversion and the per-mode degradation.
- **The knowledge corpus.** `enterprise-knowledge-base` owns governed retrieval. The quality of the corpus is the
  quality of the suggestions, and it is not this repo's asset.
- **The review console.** Escalations are routed to `human-review-console`; the console is that system.
- **Trace collection.** Spans go to `agent-observability`; `COMPLIANCE.md` R2 records that binding as open.
- **Network perimeter.** `infra/terraform/vpc_sc.tf` stands up a dry-run-first perimeter, but
  private endpoints and a distinct agent identity are recorded as open in `COMPLIANCE.md` P-09.
