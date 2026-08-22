# FAQ: portability and exit

For architecture, cloud and procurement. The question behind all of these is the same one: if we
adopt this, how do we leave?

## What exactly is cloud-specific here?

The `adapters/gcp/` directory and nothing else. The domain
(`src/contact_centre_conversations/domain/`) is pure stdlib: no web framework, no cloud SDK, no HTTP. Every
boundary is a `@runtime_checkable` Protocol in `ports/`, and which implementation binds is a line
in `config/settings.yaml`, so switching a port is configuration rather than a code edit.

Thirteen ports carry the whole boundary: `audit`, `identity`, `review_router`, the tracer, the
evaluation gate, `retrieval`, `generation`, `guardrail`, `tool_catalog`, `contact_store`, and the
three speech ports (`speech_to_text`, `text_to_speech`, `diarization`) plus
`conversation_channel`.

## What are the three profiles?

One environment variable, `CONTACT_PROFILE`, selects the family.

| Profile | What it is | Cloud SDK |
|---|---|---|
| `local` | The SDK-free offline stack: a fixture knowledge corpus, scripted contact streams, seeded dev personas, a hash-chained SQLite WORM audit log, and a deterministic drafter that composes from the retrieved passages. This is what the gate, the demo and CI run. | none |
| `gcp` | Managed: Gemini generation, the Hrz1 guardrail gateway, Hrz2 retrieval, streaming Speech-to-Text, Chirp synthesis, diarization, Firestore, Cloud Logging WORM, IAP identity. Every SDK import is LAZY, inside the method. | lazy |
| `onprem` | Fail-fast placeholders that RAISE. The client wires its own model gateway, screening service, knowledge base and speech stack. | none |

`onprem` raising rather than silently succeeding is the point. A placeholder that returned a
plausible empty answer would make the portability claim unfalsifiable, and on the guardrail port
it would be worse than unfalsifiable: an adapter that returned CLEAN without screening would make
the control decorative. `tests/contract/test_behavioral_parity.py` proves the offline family
ANSWERS and the exit family REFUSES, and `make portability` runs the same tour as named checks
with a pass or fail each and prints what it does NOT prove.

## Prove the offline profile does not need the cloud SDK.

`tests/contract/_sdk_free_probe.py` imports every module with the cloud SDKs unimportable. If a
`google.` import ever escapes a method body into module scope, the offline gate goes red on the
next run rather than in somebody's air-gapped environment six months later. The whole `make gate`
is offline, credential-free and network-free by design.

## How degraded is the offline profile, honestly?

Less degraded than you might expect, because nothing consequential was ever the model's job. All
six engines (intent, procedure, disclosure, action, policy gate, handoff) run identically. What
changes is the prose: the offline drafter composes a reply from the leading sentence of the
highest-scoring retrieved passage behind a fixed acknowledgement. That is deliberately less
capable than a model and exactly as GROUNDED, which is the property the offline gate has to be
able to assert. Speech is fixture transcripts rather than live audio.

## What would an on-premises deployment actually take?

[`../onprem-migration.md`](../onprem-migration.md) is the written path. The short version:
implement the `onprem` adapters against your own model gateway, screening service, knowledge base,
speech stack, contact store and audit sink. The domain, every port, the policy packs and the
whole test suite come across unchanged, because none of them knows what is behind a port.

The one adapter that deserves extra thought is the guardrail. A screening service you cannot
reach becomes `ScreenOutcome.UNAVAILABLE`, which fails closed per mode; decide what that costs
you before you go live rather than after.

## How do we get our data out?

The audit trail exports to and restores from JSON Lines, so the exit is a file copy, and
`make portability` performs an export plus a foreign reload as one of its named checks. Your
policy packs are YAML files you own and can take with you; they encode the whole of your
contact-centre policy and are deliberately not embedded in code. Contact records live behind
`ports/contact_store.py`, which is a Firestore collection under `gcp` and a local store offline.

## Is the audit trail portable, and is it tamper-evident?

Both. It is append-only and hash-chained, and the chain head is anchored to a file on another
volume, so an edit, a deletion, a reorder AND a truncated tail are all detectable.
`tests/unit/test_audit_anchor.py` proves each, including the control case that fails without the
anchor. The format is the commons' own, so an exported trail verifies outside this process.

## Where is our data, physically?

`asia-southeast1`, pinned once and enforced at deploy time rather than described.
`infra/terraform/variables.tf` validates the effective region against the residency allowlist at
plan time, `org_policy.tf` pins `constraints/gcp.resourceLocations` to that region's location
group, and every regional resource is created in it: the CMEK key ring, the WORM log bucket,
Firestore, the contact-audio bucket and the Cloud Run service. The managed recogniser is pinned
to the same value in `adapters/gcp/speech.py`, because a recogniser in another jurisdiction is a
residency breach no downstream masking undoes. `make tf-check` runs `terraform test` against a
mocked provider, so the refusals are proved with no project and no credentials.

## What are we locked into that is not GCP?

Four pinned commons packages: `hex-service-kit`, `agent-eval-kit`, `pii-kit` and
`review-kit`. They are ordinary Python packages pinned to commit shas, they contain no cloud
SDK, and a fork can vendor any of them.

The deeper couplings are architectural rather than technical, and they are deliberate: the Hrz1
guardrail gateway (a screening service you must have SOMETHING behind), the Hrz2 knowledge base
(a governed corpus you must have somewhere), and Hrz7 (a place escalations go). Replacing any of
them is an adapter; removing the concept is a different product.
