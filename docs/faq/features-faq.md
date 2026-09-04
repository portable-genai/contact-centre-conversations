# FAQ: what it does, and where it stops

For product and contact-centre operations. This is also the "what this repo owns vs what it
integrates" map.

## What are the two modes, and why are they separate?

- **agent-assist** is a whisper copilot beside a live human agent. During an active voice or chat
  contact it shows the deterministic next best step, reminds the agent of a required disclosure
  before its window closes, and offers a suggested reply grounded in passages the knowledge base
  returned. The agent decides; the panel advises.
- **self-service** is a customer-facing assistant. It resolves allowlisted intents end to end,
  refuses everything else, and hands off to a person with the context carried over.

They are separate because their risk postures are not comparable. One is internal
decision-support with a trained human between the model and the customer; the other reaches a
member of the public directly. So they are enabled independently, promoted independently through
their own `model-quality-gate` bundle, and evaluated by their own rubric set. **Both default off**, and with both
off every mode route refuses.

## What is deterministic and what is not?

Every consequential decision is deterministic, and each has its own pure-stdlib engine:

| Decision | Engine |
|---|---|
| What the customer is asking for | `domain/intent_engine.py` |
| Where the contact is in a procedure, and the next best step | `domain/procedure_engine.py` |
| Which disclosure is due, and by when | `domain/disclosure_engine.py` |
| Whether self-service may act at all | `domain/policy_gate.py` |
| Which action to take, and whether it needs maker-checker | `domain/action_engine.py` |
| Whether to hand off to a human | `domain/handoff.py` |

The model drafts prose and nothing else, from passages retrieval already returned, and the draft
is validated and discarded whole on any failure. See [`../model-card.md`](../model-card.md).

## Does a policy change need an engineer?

No. Everything a market requires lives in reviewed packs under `config/packs/`: the procedure
states and transitions, the required disclosures and their windows, the self-service intent
allowlist, the action catalog with its maker-checker flags, and the per-market cue lexicons.
`domain/packs.py` validates them at boot and stops the process on a bad pack, including a
procedure step that references a state nobody defined.

Two defaults worth knowing, because they are the shape of every default here: a missing packs
directory yields the EMPTY library, which refuses everything rather than allowing everything; and
the self-service intent list is an ALLOWLIST, so an intent nobody configured is refused rather
than attempted.

## What happens when the guardrail gateway is down?

Screening is not optional and unavailability is a verdict, not an exception. `domain/guardrails.py`
converts an unreachable gateway into `ScreenOutcome.UNAVAILABLE`, and `degradation_for` decides
what that costs PER MODE. The one thing no adapter may do is report CLEAN when it did not screen.
Configure the degradation you actually want before you go live.

## Can the assistant do something to an account?

Only what your action catalog lists, and anything consequential is HELD and ROUTED rather than
executed: it sets `requires_human_review` and is submitted to the `human-review-console` in the same call
that produced it (rule R8). `tests/unit/test_maker_checker.py` and
`tests/unit/test_review_routing.py` are the standing gates.

## What does a handoff carry?

The context, so the customer does not start again. `domain/handoff.py` decides the trigger
deterministically and `tests/unit/test_handoff_and_persistence.py` covers the transfer, including
what persists.

## Which sibling systems does this repo integrate rather than rebuild?

| Concern | Owner | How it appears here |
|---|---|---|
| Prompt-injection and abuse screening | `agent-guardrail-gateway` | `ports/guardrail.py`, a live dependency screening every turn. One of the few catalog repos where `agent-guardrail-gateway` is bound rather than owed |
| Governed retrieval | `enterprise-knowledge-base` knowledge base | `ports/retrieval.py` at `retrieval_url`. The corpus is `enterprise-knowledge-base`'s asset, not this repo's |
| Human review and maker-checker | `human-review-console` | `ports/review_router.py`, bound in all three families over the shared `review-kit`. Rule R8 |
| Model and agent promotion | `model-quality-gate` | two bundles, one per mode. `eval/run_eval.py --mode gate` is the client half and refuses off the managed profile |
| Tracing and immutable WORM audit | `agent-observability` | `ports/observability.py`; the local audit half is tamper-evident today, the shared sink is an open binding |
| Agent discovery and entitlements | `agent-registry` | the A2A card at `/.well-known/agent-card.json`, built from the same tool table the runtime binds |
| Post-contact QA and compliance scoring | **E3** conversation QA scorecard | it reads THIS repo's `kind: disclosure` pack and grades finished contacts against it. Do not build a QA scorer here |
| Consent and marketing screening | `marketing-compliance-gate` | not applicable. This service answers a contact the customer initiated and produces no marketing output |

## Why does E3 read our disclosure pack?

So a market's wording is authored once. The copilot reminds a live agent from it and the
scorecard grades against it, which means a bank cannot tighten a wording in one system and be
graded against the other. The pack is the shared artifact; neither system owns the other's
surface.

## How many surfaces are there, and do they agree?

Five: the FastAPI app, the argparse CLI, the agent tools, the embeddable micro-frontend and the
eval harness. They agree because they share the domain services rather than reimplementing them,
and each routes an escalated result to human review in the same call that produced it, so rule R8
does not hold on four surfaces out of five.

## Can we trust a demo of this?

The demo is code. Every step lives in `scripts/demo.py` and its assertion in
`scripts/walkthrough.py`, `tests/unit/test_demo_surface.py` holds the two sets equal, and
`make demo-selftest` runs the whole arc headless in CI. A claim the demo makes that nobody
verifies cannot exist, and the arc deliberately includes a step that shows a failure.
