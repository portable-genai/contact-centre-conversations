# Model card: Contact Centre AI (E1)

This is a STARTER model card. It records the model boundary as built and the controls that must be
completed before a managed deployment. The deterministic engines are the system of record; the
model is a bounded, replaceable component that drafts prose from passages somebody else retrieved.

This repo serves TWO separately gated modes with different risk postures, and the model boundary
is the same in both. What differs is who reads the output: agent-assist whispers to a trained
human who decides, self-service reaches a member of the public directly. Both default off, both
promote independently through their own Hrz4 bundle, and both are covered below.

## What the model does, and does not do

- **Does**: draft one grounded reply through `ports/generation.py`, from a redacted turn and a
  passage list the knowledge base has already returned and the guardrail has already screened.
  Under `gcp` a managed speech stack also transcribes and synthesises audio and performs
  diarization (`adapters/gcp/speech.py`), pinned to the deployment's own region.
- **Does NOT**: decide anything. The procedure state and the next best step
  (`domain/procedure_engine.py`, `domain/action_engine.py`), the intent classification
  (`domain/intent_engine.py`), the required-disclosure timing (`domain/disclosure_engine.py`),
  the self-service gate verdict (`domain/policy_gate.py`), the handoff trigger
  (`domain/handoff.py`) and the maker-checker escalation are pure deterministic engines over
  reviewed policy packs. The model states no figure and takes no action.

`ports/generation.py` is deliberately narrow, and the things it does not offer are the point:
no free-form completion, no tool-calling loop, no conversation-history parameter, nothing that
would let a caller ask the model a question the knowledge base did not already answer. A wider
port is a wider blast radius.

## Boundary and validation

- **The order is fixed: redact, then screen, then retrieve, then generate.**
  `domain/guardrails.py` owns that ordering. Every inbound turn is masked with the `pii-kit`
  jurisdiction rows, then screened for prompt injection and abuse through `ports/guardrail.py`
  (the Hrz1 gateway), and only then may retrieval or generation happen.
- **Unavailable is a verdict, not an exception a caller may ignore.** An adapter that cannot
  reach the guardrail gateway RAISES, and `TurnGuard` converts the raise into
  `ScreenOutcome.UNAVAILABLE`, which fails closed per mode via `degradation_for`. The one thing
  no adapter may do is return CLEAN when it did not screen, because that single behaviour would
  make the whole control decorative. `tests/unit/test_turn_guardrails.py` is the standing gate.
- **The draft is untrusted, schema or no schema.** The managed adapter asks for a response schema
  and a system instruction that says quote, cite, and never state a figure, and then
  `domain/suggestions.validate_draft` judges what comes back anyway: a response schema is a
  request and the validator is the guarantee. A draft is discarded whole, never repaired, if it
  is over `MAX_SUGGESTION_CHARS`, cites a passage that was not retrieved, or contains a number
  the passages did not supply.
- **Nothing auto-executes.** A consequential result sets `requires_human_review` and is routed to
  the Hrz7 console in the same call that produced it (rule R8), on the API, the CLI and the agent
  surface alike. `tests/unit/test_maker_checker.py` and `tests/unit/test_review_routing.py` are
  the gates.
- **A mode that is not enabled has no model path at all.** `domain/modes.py` resolves each flag
  in three states, both default off, and a mode enabled with no promotion bundle refuses to boot
  under any profile other than a deliberate offline `local`.
  `tests/unit/test_mode_gating.py` proves it.

## Adapters and profiles

| Profile | Generation | Guardrail | Speech | Behaviour |
|---|---|---|---|---|
| `local` | `adapters/local/generation.py` | `adapters/local/guardrail.py` | `adapters/local/speech.py` | No model. The drafter composes a reply from the retrieved passages themselves, the leading sentence of the highest-scoring passage behind a fixed acknowledgement: less capable than a model and exactly as grounded, which is the property the offline gate has to be able to assert. The same turn and corpus produce the same draft, so the citation and groundedness metrics measure the validator rather than the weather. SDK-free. |
| `gcp` | `adapters/gcp/generation.py` | `adapters/gcp/guardrail.py` | `adapters/gcp/speech.py` | Gemini, named by `CONTACT_MODEL` (`model` in `config/settings.yaml`), constrained to a response schema, with a lazy SDK import. The guardrail adapter is a thin S2S client to the Hrz1 gateway at `guardrail_url`; unconfigured means it REFUSES rather than defaulting to localhost. Streaming speech-to-text, Chirp synthesis and diarization are pinned to `settings.region`, because a recogniser in another jurisdiction is a residency breach no downstream masking undoes. |
| `onprem` | `adapters/onprem/generation.py` | `adapters/onprem/guardrail.py` | `adapters/onprem/speech.py` | Fail-fast placeholders. The client wires its own model gateway, screening service and speech stack. A refusal on generation costs a suggestion; a refusal on screening fails the turn closed, which is the correct direction. |

## Evaluation as it stands

The full picture, with the thresholds beside the metrics and the reasoning beside the
thresholds, is [`evals.md`](evals.md). That page is generated from the artifacts that gate the
build, so it cannot drift from them; this section says only what a model-risk reader needs.

**Two kinds of scoring, deliberately.** Seventeen deterministic metrics answer the questions
code can answer: was the turn allowed, was the reply grounded in a passage actually retrieved,
did an action execute against a record its caller owns, did anything personal survive into the
audit trail. A judged half answers the one it cannot: is the reply any good. A reply can be
allowed, grounded, cited, clean, and still tell a customer who has just said they cannot pay
that there is nothing to be done.

**Every metric is proved able to fail.** `tests/unit/test_eval_falsification.py` plants the
specific defect each metric exists to catch and fails the build if the metric stays green.
Twice during this work the harness caught a red input that was not one: widening the ownership
fixture removes a violation rather than creating one, and reclassifying a passage makes the
metric and the product agree. Both defects live in the product, so both tests now remove the
control itself.

**The judge is falsified too**, which matters more than it sounds: a broken metric returns a
wrong number and something notices, while a broken judge keeps returning numbers that keep
clearing the bar. `tests/unit/test_narrative_floor.py` constructs a judge that certifies
anything and one that grades nothing, and catches both. The offline judge is the default and is
chosen on the command line, never from the environment, and the offline run is proved to need no
network by removing the socket rather than by reading the code.

**Quality floors are data owned by model risk**, in `config/quality-floors.toml`, with a floor
that refuses, a target that means full quality, and a DEGRADED band between them. The
customer-facing bar is the higher one because nobody reviews that reply before a customer reads
it. The expectations are a degradation TABLE rather than a threshold, so a profile that quietly
got better fails too: a band nobody predicted is a change nobody reviewed.

**What the scores are, and are not.** Every deterministic metric scores the offline template
drafter, so `citation_accuracy` and `groundedness` measure the VALIDATOR rather than a model's
restraint: the template quoter structurally cannot invent a figure. Model quality is assessed in
the judged half, and it grades recorded text rather than a live call. Two bands there were
predicted wrong and the table said so: for a simple factual answer the template drafter is not
degraded, because quoting the passage supplies exactly the reference points and the citation the
criteria ask for. Those rows were recalibrated to what was measured, with the reasoning recorded
in the dataset, and degradation appears where a reply must do more than quote.

**Attribution.** Bands are calibrated against the deterministic judge named in the run header.
Changing judges means recalibrating the table in the same commit, because a score is only
meaningful against the judge that produced it.

**Scored over** two verticals (retail banking, general insurance) and two markets (SG, JP),
customer-facing scenarios multi-turn. The voice path carries no scenarios: word error rate per
locale and per channel needs audio corpora and is a named follow-up, not an omission.

## Remaining controls (TODO, repo owner)

- **Model version pinning.** `CONTACT_MODEL` defaults to a model family alias rather than an
  immutable version, so what runs can change under the service with no diff here. Pin the exact
  version per mode and record it in this file, along with the locales you have reviewed output
  for.
- **Budget, rate control and a kill switch.** There is no per-tenant token budget and no request
  rate limit. A kill switch is cheap here and should be explicit: turning a mode off already
  removes its model path entirely, so document that as the switch and test it.
- **Evaluation of the live model.** The offline rubrics score the deterministic drafter and the
  validator, not live Gemini output. Add a managed-profile run through the Hrz4 gate, per mode,
  that scores real drafts for groundedness and citation accuracy against the same golden sets.
- **Self-service output review before public exposure.** The gate verdict is deterministic, but
  the words a customer reads are drafted. Before enabling `self-service` for a real public,
  record who reviewed the drafted-reply corpus, in which locales, and against which conduct
  standard.
- **Speech accuracy as a measured property.** Word error rate per locale and per channel is not
  measured here. A mis-transcribed disclosure or intent degrades safely (an unmatched intent is
  refused, not guessed) but still costs a contact. Record your measured rate per market.
- **Audio and transcript retention.** Recordings and transcripts persist in the buckets and the
  Firestore collection the Terraform creates. Record who may listen, for how long, and how a
  subject-access request reaches them.

Until these are complete the system is safe to run offline (deterministic engines plus the
passage-composing drafter, no model, no audio) and the managed model path is not
production-cleared for either mode.
