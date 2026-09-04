# Adopting this repo as your base

This repository (E1, Contact Centre AI) is a **common base** that a bank, insurer or other
regulated institution forks to build its own **contact-centre conversational AI**, in two
separately gated modes over one shared kernel:

- **agent-assist**, a whisper copilot beside a live human agent: deterministic next-best-step,
  required-disclosure reminders and knowledge-base-grounded suggested replies during an active
  contact;
- **self-service**, a customer-facing assistant that resolves allowlisted intents end to end,
  refuses everything else, and hands off to a person with the context carried over.

The modes are enabled independently and promote independently, because their risk postures
differ: one is internal decision-support with a trained human in the loop, the other reaches a
member of the public directly. **Both default off**, and with both off every mode route refuses.
That two-mode split is the most important thing to understand before adopting, and it is the
first decision you have to make.

The repo ships a reusable hexagonal core (a pure-stdlib domain, thirteen typed ports, three
swappable adapter families, a green offline gate) plus a fully worked contact-centre vertical
you keep, retune, or replace with your own policy packs.

This guide is the step-by-step for making it yours. It has two halves: a **mechanical rebrand**
(one script) and the **human decisions** the script cannot make for you.

> Related reading: [`../ARCHITECTURE.md`](../ARCHITECTURE.md) (the port table and the topology),
> [`../CONTRIBUTING.md`](../CONTRIBUTING.md) (the file-by-file touch list for a new port or
> adapter), [`model-card.md`](model-card.md) (the model boundary), and the [`faq/`](faq/)
> directory.

---

## 1. What you keep vs what you rewrite

The boundary between reusable machinery and your contact-centre policy is a physical module
split. `domain/kernel.py` holds the vertical-neutral machinery; `domain/contact_kernel.py` holds
the conversation primitives; the engines know no market and no wording, because every wording
lives in a reviewed pack.

| Layer | Where | For your deployment |
|---|---|---|
| **Vertical-neutral machinery** | `domain/kernel.py`, `domain/contact_kernel.py`, `domain/errors.py`, `domain/pii.py`, every Protocol in `ports/`, the container wiring in `config.py`, and the commons (`hex-service-kit`, `pii-kit`, `review-kit`, `agent-eval-kit`) | keep untouched |
| **Policy (your packs and your numbers)** | `config/packs/`: the procedure packs, the disclosure pack, the self-service intent allowlist, the action catalog and the per-market cue lexicons. Validated at boot by `domain/packs.py`, which stops the process on a pack that fails. Plus the mode flags and promotion bundles in the `modes:` block of `config/settings.yaml` | change by configuration, never by editing an engine |
| **Vertical (the artifacts)** | `domain/models.py`, the six engines (`intent_engine.py`, `procedure_engine.py`, `disclosure_engine.py`, `action_engine.py`, `policy_gate.py`, `handoff.py`), `domain/suggestions.py`, the knowledge-base corpus and scripted streams under `config/`, the two eval golden sets and the UI views | reseed for your data; rewrite only if your contact model genuinely differs |

A missing packs directory yields the EMPTY library, which refuses everything rather than allowing
everything. That is the shape of every default in this repo, and a fork should keep it.

## 2. Core-vs-adopter-owned files (so upstream merges stay mechanical)

- **Upstream-owned** (take our changes): `domain/kernel.py`, `domain/contact_kernel.py`, the six
  engines, `domain/guardrails.py`, `domain/suggestions.py`, `ports/`, `tests/contract/`, the eval
  harness mechanics (`eval/run_eval.py`), the CI workflows, and the `Container` wiring in
  `config.py`.
- **Adopter-owned** (yours; expect to edit): everything under `config/packs/`, the knowledge-base
  corpus and the scripted streams, `adapters/onprem/*`, UI theming and branding, both golden eval
  datasets in `eval/datasets/`, and the regulator crosswalk section of
  [`../COMPLIANCE.md`](../COMPLIANCE.md).

Track upstream by git tag, and rebase your adopter-owned changes onto each release rather than
merging `main` continuously.

## 3. The mechanical rebrand (one script)

`scripts/rename_fork.py` rewrites the package name (`contact_centre_conversations`, which is also the
console-script name), the `CONTACT` environment prefix, the distribution and resource id
(`contact-centre-conversations`) and the Terraform `name_prefix` default, in one pass. Preview first,
then apply:

```bash
# Preview (writes nothing):
python scripts/rename_fork.py --package acme_contact_centre \
    --env-prefix ACMECONTACT --resource acme-contact-centre \
    --name-prefix acme-contact --dry-run

# Apply:
python scripts/rename_fork.py --package acme_contact_centre \
    --env-prefix ACMECONTACT --resource acme-contact-centre \
    --name-prefix acme-contact --yes

# Then recreate the environment (the distribution name changed) and prove it is green:
python3.12 -m venv .venv && source .venv/bin/activate
make install
make gate
```

There is deliberately no `--cli` flag: `[project.scripts]` names the console script after the
package, so `--package` renames both and a second flag could only drift out of step. There is no
`--dist` flag either: the distribution name, the GitHub id in `[project.urls]` and the A2A
agent-card name are the same one literal, and `--resource` renames it.

One thing the script does NOT rename, on purpose: the `model-quality-gate` promotion bundle ids
(`CONTACT_AGENT_ASSIST_BUNDLE`, `CONTACT_SELF_SERVICE_BUNDLE`) are deployment values rather than
source literals, and a promotion record that silently changed identity would be worse than one
you had to set by hand. Set them yourself, per mode. Add `--include-docs` to sweep Markdown prose
too. The script deliberately does NOT touch the human decisions below.

## 4. The human decisions (the script can't make these)

1. **Which modes you run, and when.** Both flags default off and both are read in three states:
   unset is off, deliberately EMPTIED refuses to boot rather than inheriting that default, and so
   does an unrecognised or mis-capitalised value. A mode enabled with no promotion bundle refuses
   to boot under any profile except a deliberate offline `local`, where there is no customer and
   nothing to promote to. Turn on `agent_assist` first: it has a trained human between the model
   and the customer. `self_service` reaches the public and deserves its own risk decision, its
   own bundle and its own sign-off. `tests/unit/test_mode_gating.py` is the standing gate.
2. **Region and residency.** The build pins `asia-southeast1`. Change it in BOTH places: `region`
   in `config/settings.yaml`, and the Terraform `region` and `allowed_regions` pair in
   `infra/terraform/variables.tf`, which are validated against each other at plan time. The
   managed recogniser is pinned to the same value in `adapters/gcp/speech.py`, because a
   recogniser in another jurisdiction is a residency breach no downstream masking undoes. Prove
   the change with `make tf-check`, which runs `terraform test` against a mocked provider and
   needs no project and no credentials. See [`runbook.md`](runbook.md).
3. **Identity and your IdP.** This repo owns no login flow. Under `gcp` the identity adapter
   verifies the Cloud IAP-injected assertion and refuses when `CONTACT_IAP_AUDIENCE` is unset or
   emptied; under `local` it seeds dev personas that authenticate nobody; under `onprem` it is a
   client-IdP placeholder that raises. Configure IAP on the deployed service and set the
   audience, or implement the `onprem` adapter against your own issuer.
4. **The policy packs, which are your conduct position.** This is the main act of adoption.
   `config/packs/` ships one worked example of each kind and every one of them is synthetic:
   a card-dispute procedure, a retail disclosure pack, a self-service intent allowlist, an action
   catalog and a market cue lexicon. Author your own, per market AND per vertical: the procedure
   states and their transitions, which disclosures are required and how soon, which intents
   self-service may resolve at all (an allowlist, so an intent nobody listed is refused rather
   than attempted), which actions exist and which need maker-checker, and the vulnerability and
   sentiment cues. Every pack declares a `vertical`, the line of business whose reviewed policy
   it carries, and packs are selected by `(market, vertical)`: a bank and an insurer both operate
   in SG and their procedures are different reviewed artifacts. Two packs of one kind claiming
   the same key is a boot failure naming both, never a race to sort first.
   `domain/packs.py` validates at boot and stops the process on a bad pack, including dangling
   references between procedure steps.
5. **The knowledge base.** Under `local` the corpus is a JSON Lines fixture at `kb_path`. Under
   `gcp` and the platform family, retrieval goes to `enterprise-knowledge-base` at `retrieval_url`, and an unconfigured
   remote REFUSES at the adapter rather than defaulting to localhost. Whichever you use, the
   model only ever drafts from passages retrieval already returned, so the quality of the corpus
   IS the quality of the suggestions.
6. **The guardrail gateway.** `guardrail_url` points at `agent-guardrail-gateway`. Screening is not optional here:
   `domain/guardrails.py` screens every turn after redaction and before retrieval or generation,
   and an unreachable gateway becomes `ScreenOutcome.UNAVAILABLE`, which fails closed per mode.
   Decide, per mode, what an unavailable screen should cost you, and set it deliberately.
7. **Reference data is fictional.** Every pack, corpus passage, scripted stream, contact id and
   eval case uses obviously fake parties and `.example` domains. Replace them with your own
   synthetic data. **Do not run against real contacts without your own legal, privacy, conduct
   and model-risk sign-off.**
8. **Two eval golden sets, not one.** `eval/datasets/` carries an agent-assist set and a
   self-service set, scored by separate rubrics against separate `model-quality-gate` bundles. Rebuild both for
   your packs: a fork inherits a green gate that measures the WRONG policy until you do. Note
   that `containment` has a deliberately modest threshold, because a self-service assistant that
   contains too much is a worse outcome than one that hands off; choose your own number with your
   conduct function rather than raising it because a dashboard looks better.
9. **Deployment posture.** Review the Dockerfile (digest-pinned base, non-root),
   `infra/terraform/` (Org Policy, CMEK, the VPC-SC perimeter, the locked WORM log bucket,
   Firestore and the contact-audio bucket) and the loopback-by-default API binding before you
   expose anything.

## 5. Do not duplicate the platform

This repo is one system in a catalog of composable GRC systems. Several concerns it *touches* are
owned by sibling services; integrate rather than rebuild them. See
[`faq/features-faq.md`](faq/features-faq.md) for the full boundary map.

- `agent-guardrail-gateway` agent guardrail gateway: bound through `ports/guardrail.py` and screening every turn.
  This is one of the few catalog repos where `agent-guardrail-gateway` is a live dependency rather than an open
  binding, because untrusted customer text reaches a model here.
- `enterprise-knowledge-base`: bound through `ports/retrieval.py`. Passages come from a
  governed corpus; this repo does not build a second one.
- `human-review-console`: every consequential result is routed there in the same call that
  produced it, over the shared `review-kit` (rule R8). You wire your endpoint; you do not
  re-implement the console.
- `model-quality-gate`: owns promotion, per mode, through the two bundles.
  `eval/run_eval.py --mode gate` is the client half and refuses to run off the managed profile.
- `agent-observability` and immutable WORM audit: trace spans and audit events go there.
- `agent-registry`: this agent publishes its A2A card at
  `/.well-known/agent-card.json` for discovery.
- **E3** conversation QA scorecard (`conversation-qa-scorecard`): the post-contact review half
  of the same market obligations. It reads THIS repo's `kind: disclosure` pack and grades against
  it, so a wording is authored once. Do not build a QA scorer here.
- `marketing-compliance-gate` consent and marketing screening: not applicable. This service answers a contact the
  customer initiated and produces no marketing output.

## 6. Adoption checklist

- [ ] Ran `scripts/rename_fork.py`, recreated the venv, `make gate` green.
- [ ] Decided which modes you run, set their flags deliberately, and set a promotion bundle for
      each one you enable.
- [ ] Set the region in `config/settings.yaml` AND the Terraform `region` / `allowed_regions`
      pair, and `make tf-check` still passes.
- [ ] Configured IAP on the deployed service and set `CONTACT_IAP_AUDIENCE`, or implemented the
      `onprem` identity adapter.
- [ ] Authored your own policy packs, with conduct signing off every disclosure, every allowlisted
      intent and every maker-checker action.
- [ ] Pointed retrieval at your governed corpus or at `enterprise-knowledge-base`, and reviewed the corpus itself.
- [ ] Pointed `guardrail_url` at `agent-guardrail-gateway` and decided what an unavailable screen costs, per mode.
- [ ] Replaced every synthetic pack, passage, stream and fixture.
- [ ] Rebuilt BOTH eval golden sets and chose your own `containment` threshold.
- [ ] Reviewed the deploy posture (Dockerfile, Terraform, bind address) before exposing anything.
- [ ] Wired your `human-review-console` endpoint and decided which sibling services you integrate vs stub.
- [ ] Recorded your baseline upstream tag so you can take future fixes.
