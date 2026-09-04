# ARCHITECTURE: Contact Centre AI (E1)

Hexagonal ports-and-adapters. A pure-stdlib domain core speaks only to ports (`typing.Protocol`s);
adapter families implement them; one env var (`CONTACT_PROFILE`) swaps the
whole stack with no domain edits.

Profile selection is an exact lookup. Every declared profile has an entry for every port; when
two profiles intentionally reuse one adapter, both entries name it. A missing local or exit
binding never inherits `gcp`, so it cannot import a managed SDK or change data custody silently.

`local` runs the real API, orchestration and deterministic domain with local or synthetic edges.
It may reduce transcription/narration quality, throughput, durability, enterprise identity,
managed safety and telemetry, but it does not change figures, evidence links, escalation rules
or schemas. `make portability` executes this boundary. A construction-only primary managed
operation must block API startup and Terraform serving authorization until its integration test
exists.

## Layout (`src/contact_centre_conversations/`)
- `domain/` : pure stdlib, no cloud/framework imports.
  - `kernel.py` : vertical-neutral types (`Citation`, `AuditEvent`, the commons `StrEnum`
    taxonomies). The audit event carries the MODE that produced it.
  - `modes.py` : the two-mode gate. Both modes born OFF, resolved three-state, refusing at boot.
  - `models.py` : this vertical's artifacts. Transcript, speaker turn, word offset, channel role
    and redaction span are NOT redeclared; they come from `speech-lexicon-kit`.
  - `packs.py` : the reviewed policy, as validated data (procedure, disclosure, allowlist, action
    catalog, customer cues). Pure parsing; `config.py` is the only thing that opens a file.
  - the engines, one decision each and all pure: `procedure_engine.py` (state and next best
    step), `disclosure_engine.py` (reminder timing), `intent_engine.py` (deterministic match
    quality), `policy_gate.py` (fail-closed allowlists, worst-wins), `action_engine.py`
    (parameter validation and the maker-checker line), `handoff.py` (triggers, package,
    carry-over), `suggestions.py` (the grounding checks), `guardrails.py` (redact then screen).
  - `contact_kernel.py` : what both modes share. `assist_service.py` and `self_service.py` : what
    they do not.
  - `pii.py` (the jurisdiction pattern selection and order), `errors.py` (the domain refusals
    that carry their own HTTP status).
- `ports/` : `@runtime_checkable` Protocols, re-exported once with the `PORT_PROTOCOLS` map. The
  identity port comes from the commons and the three SPEECH ports come from
  `speech-lexicon-kit`, for the same reason: one owner per boundary. `retrieval.py` carries the
  `enterprise-knowledge-base` governed-RAG shape and the PROPOSED remote contract, written down because no built repo
  shipped one before this. `identity.py` adds
  this service's own identity vocabulary: what an adapter DECLARES about the end-user
  authentication it provides (`VERIFIED` / `CLIENT_ASSERTED` / `UNIMPLEMENTED`), which is what the
  loopback exposure guard reads, plus the refusal type that carries a status and a reason when no
  end user can be authenticated at all.
- `adapters/{local,gcp,onprem}/` : one adapter per port per profile. GCP imports are lazy.
  `adapters/_review_payload.py` is the shared, redacted conversion to the review kit's wire shape.
- `config.py` : `Settings` + `Container` (lazy DI, dotted `module:Class` bindings loaded from
  `config/settings.yaml`). It also resolves the mode gates and LOADS AND VALIDATES the policy
  packs, so a broken pack is a boot failure rather than a rule that silently never fires.
- `services.py` : the composition root. Ports in, the two mode services out, plus `require_mode`,
  which every surface calls before it does any work.
- `api/` : FastAPI app wired with the commons identity / S2S / fail-closed helpers. Two mode
  routes, `POST /v1/agent-assist/turn` and `POST /v1/self-service/turn`, each gated separately.
  `/healthz` reports the mode posture, because "up but every mode route answers 503" is the most
  confusing state this design can be in.
- `cli/` : a stdlib argparse CLI that replays a scripted contact through either mode.
- `agent/` : the optional-but-scaffolded agent surface. `tools.py` holds plain Python callables
  that delegate to the domain services (no business logic of their own) and route escalations
  like every other surface; `agent_card.py` builds the A2A discovery card served at
  `/.well-known/agent-card.json`. Nothing here needs ADK or a cloud SDK to import or test:
  `build_function_tools()` is the single lazily-imported runtime seam.

## Configuration outside `src/`
- `config/settings.yaml` : the mode block, the pack and fixture paths, the sibling service URLs,
  and the per-port adapter binding table.
- `config/packs/` : the bank's reviewed policy as data. Changing a required disclosure is an edit
  here plus a pack-schema check, never a code change and never a release of this service.
- `config/kb/` and `config/streams/` : the offline fixture knowledge base and the scripted
  contacts the local retrieval, speech and channel adapters replay.

## Surfaces outside `src/`
- `scripts/` : the demo surface. `demo.py` holds the scripted arc and drives the REAL services;
  `render_ui.py` paints its panels as dependency-free static HTML; `demo_server.py` serves the
  same panels live, one real step per click; `walkthrough.py` drives that server over loopback
  HTTP and asserts every step, which is what lets the presenter tool double as the unattended
  self-test. `portability_demo.py` and `check_docs_links.py` are standalone checks. Nothing here
  is imported by `src/`, and `.dockerignore` keeps all of it out of the serving image.
- `ui/` : the embeddable Next.js micro-frontend. Its security boundary is one policy module
  (`lib/embed-policy.mjs`) shared by the document-layer `proxy.ts` and the same-origin API route,
  plus one server-side identity module (`lib/server/identity.ts`). The browser never asserts an
  actor and never holds the service credential. Delete it with `make drop-ui` if this repo has no
  user-facing surface; the gate checks that decision for consistency in both directions.

## Test layout (`tests/`)
`unit/` (one module or service, driven by the REAL local adapters), `contract/` (the boundary
claims: conformance, the five-way port drift guard, behavioural parity), `integration/` (needs a
live service; marked so the offline gate deselects the whole directory) and `fixtures/` (shared
data only). `contract/canonical.py` holds ONE canonical request per port, so the structural and
behavioural suites cannot quietly assert different things.

## Request pipeline (one turn, either mode)
redact-before-audit (P-04) -> deterministic severity band -> soft escalation (P-06) -> already
redacted WORM audit write -> **route the escalation to `human-review-console` (R8)**. The audit actor and the
review maker are both the verified `Principal`, never the request body. Routing happens in the
same request that produced the result, on the API and CLI surfaces alike, so an escalation never
depends on a later job that may not exist.

## The port table
| Port | local | gcp | onprem |
|---|---|---|---|
| `AuditSinkPort` | hash-chained SQLite WORM (commons) | Cloud Logging WORM (lazy) | placeholder |
| `IdentityPort` | seeded personas (commons) | IAP assertion (lazy) | placeholder |
| `ReviewRouterPort` | review-kit outbox (offline, inspectable) | `human-review-console` service intake over S2S | placeholder |

The on-prem placeholders RAISE. A review router that silently returned would convert every
consequential result into an unreviewed one, which is worse than a missing feature.

A port is registered in FIVE places: `ports/__init__.py` (`PORT_PROTOCOLS`), `config.py`
(`DEFAULT_BINDINGS` and a `Container` accessor), `config/settings.yaml` and
`tests/contract/canonical.py`. `tests/contract/test_port_parity.py` asserts set equality across
all five, so a port that is bound but unregistered (or registered but unbound) fails the build
instead of running with no enforcement. The full touch list is in `CONTRIBUTING.md`.

## Audit integrity
The local WORM log is hash-chained AND anchored: `audit_anchor_path` points at an external file,
on a different volume, that every append writes the chain head to. The chain alone catches an
edit, a deletion or a reorder; only the anchor catches a truncated tail, because a truncated
chain still verifies. `tests/unit/test_audit_anchor.py` proves both halves, including the
control case where the same truncation goes undetected without an anchor.
