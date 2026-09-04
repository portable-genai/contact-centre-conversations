# SPEC: Contact Centre AI (E1)

Locked decisions, pinned stack, contracts. This document is the deepest authority on intent.

## Pinned stack
- Python `>=3.12`; ruff pinned exactly (`0.15.18`); mypy strict; deploy region `asia-southeast1`.
- Commons declared by tag in `pyproject.toml` (`pii-kit@v0.0.1`, `hex-service-kit@v0.0.1`, `agent-eval-kit@v0.0.1`, `review-kit@v0.0.1`, `speech-lexicon-kit@v0.0.1`) and pinned in the lockfiles to the 40-character COMMIT each tag resolved to. A tag can be moved; a commit cannot, so a lockfile that pinned the tag would let what installs change with no diff. `tests/unit/test_repo_artifacts.py` asserts the three-way agreement offline.
- The `hex-service-kit` pin is a security floor, not a preference: the kit checks the
  service-identity policy before the token, gates the zero-secret local opening on an exact
  profile match, and binds the loopback exposure guard over both HTTP and WebSocket scopes; it
  resolves every environment read in three states, so a variable set to empty fails closed
  instead of inheriting the unset default. Never move this pin backwards.
- Installs are LOCKED: `requirements-dev.lock` and `requirements-gcp.lock` are committed and are
  what `make install`, CI and the container image install. Nothing ships from an uncommitted
  resolve.

## What this service is

One repository, one shared kernel, TWO separately gated modes.

- **agent-assist** is a whisper copilot beside a live human agent on a voice or chat contact. It
  shows where the procedure has got to, the single next best step, the disclosure reminders that
  are due, and a knowledge-base-grounded suggested reply.
- **self-service** is a customer-facing assistant that resolves allowlisted intents end to end,
  refuses everything else, and hands off to a person with the context carried over.

The kernel they share is transcripts, the knowledge base and the audit trail. Everything else is
per mode, and the two modes are separate `model-quality-gate` gated releases because their risk postures differ:
one is internal decision-support with a trained human in the loop, the other reaches a member of
the public directly.

## Contracts
- **Two-mode gating**: `modes.agent_assist` and `modes.self_service` in `config/settings.yaml`,
  BOTH DEFAULT OFF, each resolved in three states. UNSET takes the file's written `off`;
  SET-AND-EMPTY refuses to boot rather than inheriting it; an unknown or mis-capitalised value
  refuses to boot. A mode enabled with no `promotion_bundle` refuses to boot under any profile
  other than a deliberate offline `local`, and a mode enabled when no profile was chosen refuses
  outright. With both flags off, every mode route on every surface (API, CLI, agent tools)
  answers 503. Enabling one mode grants nothing to the other.
- **Policy is DATA**: procedure packs, disclosure packs, per-tenant allowlist packs, the action
  catalog and the customer-cue lists live in `config/packs/` and are validated at boot. A pack
  that names a state, an action or a lexicon entry nothing defines is a boot failure, not a rule
  that silently never fires. The disclosure pack shape is deliberately the one E3
  (`conversation-qa-scorecard`) reads, so a market's requirement is reviewed once and cannot
  drift between the live copilot and the post-contact scorecard.
- **The next best step is chosen by code**: `domain/procedure_engine.py` walks the procedure from
  phrase-match hits over the live transcript and emits ONE step, which is the sentence the pack
  author wrote. The model does not rank steps, paraphrase them or see the engine's inputs.
- **Disclosure timing is arithmetic**: trigger offsets plus the pack's window. A reminder never
  fires before its trigger. A window that closes unsatisfied is MISSED, which sets
  `requires_human_review` and routes to `human-review-console` under R8. Where the transcript carries turn timings
  but no word timings the engine falls back to the TURN's bounds, which can only report a
  disclosure as later than it was, never earlier.
- **The self-service gate is two fail-closed allowlists**, per tenant and market: the intents it
  may HANDLE and, separately, the actions it may TAKE. An empty allowlist refuses BEFORE anything
  else is evaluated. Unmatched, ambiguous, or below the configured confidence floor all DENY.
  Verdicts compose worst-wins, so adding a check can only tighten the gate. The confidence is
  computed by `domain/intent_engine.py` from phrase coverage and distinctness; it is never a
  model's number.
- **Consequential actions never auto-execute**: an action the catalog marks `consequential`
  yields a pending-review case and ZERO calls to the executor port, whatever the gate decided.
  The proof is a spy adapter that counts calls.
- **Handoff triggers are deterministic**: a blocked or unavailable guardrail screen, a
  vulnerability cue, an explicit request for a person, a gate denial, repeated failed intents, or
  a consequential action, in that fixed precedence. The package is schema-checked by its
  PRODUCER and carries the redacted transcript, the procedure state, the gate verdicts and any
  pending action. The carry-over is the COMPLETED state ids, replayed through the same engine on
  the receiving side.
- **Every inbound turn is redacted THEN screened**, in that order, by one object every turn
  passes through. Only a CLEAN screen may reach retrieval or generation. Screen unavailable fails
  closed per mode: agent-assist degrades to deterministic-only, self-service hands off.
- **Empty retrieval means no suggestion.** A drafted reply is discarded whole on any schema
  failure, on a citation naming a passage that was not retrieved, or on any figure the cited
  passages do not contain.
- **Identity**: a request's actor is a server-verified `Principal`; the client-supplied actor is
  discarded. Local profile resolves a seeded dev persona from `X-Dev-Persona`.
- **Redaction before anything**: `pii-kit` masks a turn before it is stored, before it reaches
  the knowledge base, before it reaches a model and before it reaches the audit record.
- **Determinism**: the procedure state, the next best step, the disclosure verdicts, the intent
  confidence, the gate outcome, the action decision and the handoff trigger are pure stdlib and
  replayable; a model may draft a cited suggestion and produces nothing else.
- **Maker-checker (P-06) and routing (R8)**: a missed disclosure window or a consequential action
  sets `requires_human_review=True` AND is routed through `ReviewRouterPort` to the `human-review-console`
  in the same call. The review is TAGGED with the mode that produced it, in the action and in the
  segregation-of-duty group, so one mode's checkers cannot sign off the other's escalations. The flag alone is not the escalation. The response carries `review_ref`, so a
  caller can tell a routed escalation from one that stopped here. The managed adapter refuses to
  run with no console configured rather than swallowing the escalation.
- **Profile**: resolved ONCE, at import, into a `ProfileChoice` and never a bare string. Three
  states of `CONTACT_PROFILE`: UNSET is NO CHOICE (the SDK-free adapters
  still bind, but the seeded personas are refused, no service-to-service scheme is selected, every
  relaxation sees `unconfigured` and the exposure guard refuses every route to a non-loopback
  peer); SET AND EMPTY raises, so it can never inherit the unset behaviour; SET AND UNKNOWN,
  including a mis-capitalised value, raises. Only a deliberately named profile is honoured, and
  both raises happen before the process can serve anything.
- **Two derived postures, opposite directions**: `exposure_profile` drives every RELAXATION (CORS
  allowlist, the `X-Dev-Persona` allowed header, the HSTS baseline, the S2S scheme) and reads
  `unconfigured` when nobody chose; `bind_profile` drives the RESTRICTION (the loopback bound) and
  reads `local` when nobody chose. One string cannot do both without weakening one of them.
  Only `config.py` reads the variable.
- **End-user authentication is a property of the identity BINDING**, declared by the adapter
  (`VERIFIED` / `CLIENT_ASSERTED` / `UNIMPLEMENTED`) and read by the loopback exposure guard. The
  service-to-service secret authenticates a calling SERVICE and no end user, so it takes no part
  in that decision: setting it closes the S2S routes and relaxes nothing.
- **Audit integrity**: the trail is hash-chained AND externally anchored. `audit_anchor_path`
  points at a file on a different volume that every append writes the chain head to; without it
  a truncated tail is undetectable, because the shorter chain still verifies. Once store and
  anchor disagree the service refuses to append rather than re-anchoring, so an ordinary write
  cannot launder a divergence. Re-anchoring is a deliberate operator action.
- **Agent surface**: optional but scaffolded. The A2A card at `/.well-known/agent-card.json` is
  built from the same tool table the runtime binds, so advertised skills and implemented tools
  are the same set. Tool results are masked for personal data before they return, because a tool
  result becomes model context (P-04); an API response to the caller who supplied the text is
  not. Nothing in `agent/` needs a runtime to import; `build_function_tools()` is the only seam.
- **Ports**: a port is registered in five places (`PORT_PROTOCOLS`, `DEFAULT_BINDINGS`, the
  `Container` accessor, `config/settings.yaml`, and the canonical-call table) and the contract
  suite asserts set equality across all five, in both directions.
- **Demo**: the demo is code and it is asserted. `scripts/walkthrough.py` narrates its steps
  and, at each one, checks that the service actually reached the state the narration claimed;
  `--auto --headless` runs the same steps unattended in CI. A step exists in exactly two places
  (`demo.STEPS` and `walkthrough.CHECKS`) and the two are held equal, so a narrated claim nobody
  verifies cannot exist. The demo needs no browser engine, no network and no cloud.
- **UI identity**: the browser never asserts who it is. Every client-supplied actor, tenant,
  role, ACL and authorization header is discarded before a request is forwarded; identity is
  resolved server-side and the resolved headers are attached afterwards. The service credential
  is read from the server environment only. Framing and CORS are allowlists that refuse a
  wildcard however it is written, and an empty allowlist denies rather than opening up.
- **Persistence**: the contact store is tenant-scoped and the domain authorises against the
  VERIFIED principal. A cross-tenant read answers **403, not 404**: a contact id is not a secret
  in this vertical (it is the customer's own reference and it is in the channel's logs), so
  hiding existence buys nothing and costs an operator the difference between "not yours" and
  "lost".
- **Eval**: TWO rubric sets, reported SEPARATELY, because the two modes promote separately.
  `--rubric agent_assist|self_service|both`; `--mode smoke` is the offline pre-merge check and
  `--mode gate` is the `model-quality-gate` promotion authority for the named rubric's own bundle. Every metric
  scores against the dataset's own expected label, never against the pipeline's verdict, and
  every metric is proved able to go red.
- **Tests**: split into `unit`, `contract` and `integration`. The offline gate runs the first
  two; every integration module is marked, and that marking is itself enforced.

## Metrics and thresholds (smoke)

Two rubric sets, reported separately. Each `model-quality-gate` promotion gate consumes only its own bundle
(`contact-centre-conversations-agent-assist`, `contact-centre-conversations-self-service`), so a strong result
in one mode can never carry the other over the line.

**agent-assist** (`eval/datasets/agent_assist_golden.jsonl`)
- `next_step_accuracy >= 1.0`
- `reminder_timeliness >= 1.0` (fires inside the window; never without its trigger)
- `citation_accuracy >= 1.0`
- `groundedness >= 1.0`
- `pii_safety >= 0.99` (pack scan plus a pack-independent planted-literal check)

**self-service** (`eval/datasets/self_service_golden.jsonl`, including adversarial out-of-scope
asks)
- `gate_precision >= 1.0`. A customer-facing gate that is right most of the time is worse than
  no gate, because it is trusted. Proved able to fail by injecting a wildcard intent into a test
  allowlist, which the shipped pack schema cannot express.
- `handoff_safety >= 1.0`
- `maker_checker_safety >= 1.0`
- `containment >= 0.2` on non-adversarial rows. Containment is measured, never pursued: nothing
  in the code tries to keep a contact in self-service.
