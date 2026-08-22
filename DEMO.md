# DEMO: Contact Centre AI (E1)

Everything here runs **offline**: no cloud project, no credentials, no API key, no browser
engine, no bundler. That is the first thing to say out loud, because it is the claim the rest of
the demo rests on.

```bash
make install          # locked install from requirements-dev.lock
make demo             # the presenter-paced walkthrough (starts its own server)
```

## The walkthrough

`make demo` starts a loopback server, opens the page, and then waits for you at every step. The
narration is printed on **your terminal**, never on the page, so the audience sees only the clean
output view. At a prompt: **Enter** runs the step, a **number** jumps to that step, **r** restarts
the run, **q** quits.

Every step drives the real services. Nothing is pre-recorded, and every step is ASSERTED: if the
service does not actually reach the state the narration just claimed, the walkthrough says so and
exits non-zero.

| # | Step | The point to make |
|---|---|---|
| 1 | Bound offline, and BOTH modes gated | One variable binds every port. And the two modes are separate releases: enabled independently, both default off, and with both off every mode route refuses. |
| 2 | Agent-assist: the whisper panel | The procedure state, the next best step and the reminders come from pure engines over reviewed packs. The step is the pack author's sentence; the model never picks one. |
| 3 | A disclosure window closes unsatisfied | The reminder is due, the contact ends, the window is missed. Routed to the human-review console in the same call (rule R8). Setting the flag is not the escalation; routing is. |
| 4 | A turn carrying personal data | The identifier is masked before the store, before the knowledge base and before the immutable record. Masking afterwards would be too late three times over. |
| 5 | Self-service: what the allowlists refuse | One allowlisted ask answered, one out-of-scope ask denied and handed off, one prompt injection blocked before it reaches a model at all. |
| 6 | A consequential action | The gate reaches review rather than allow, the executor is called ZERO times, and a pending-review case goes to the console. The count is the proof, not the flag. |
| 7 | The audit trail | Hash-chained, externally anchored, exportable to JSON Lines, and every record tagged with the MODE that produced it. |
| 8 | A tampered record | An attacker with file access rewrites a record; the chain names exactly which one. Tamper-EVIDENT, not tamper-proof. |
| 9 | The exit profile | The same calls on `onprem`, no code edited: every unimplemented seam refuses loudly rather than dropping the work. |

Steps 6 and 8 are the ones to linger on. Step 6 is a claim about something that did NOT happen,
and the panel shows the adapter call count rather than asking you to believe a boolean. Step 8 is
a demo where something goes wrong on purpose: a demo where nothing ever does is a sales deck.

## The other three ways to run it

```bash
make demo-selftest    # unattended and headless, asserts every step, non-zero on failure
make demo-static      # demo.json plus out/index.html and out/step-*.html, for screenshots
make portability      # the executable portability claim: named checks, pass or fail each
```

`make demo-selftest` runs in CI on every push (`.github/workflows/demo-gate.yaml`), so the demo
cannot rot silently between showings. `scripts/README.md` documents each script and the
environment overrides.

## The claims, and their bounds

State the bounds yourself. An unbounded claim is the one an auditor disproves for you.

| Claimed | Proved by | NOT claimed |
|---|---|---|
| Runs with no cloud, credentials or network | the whole demo, plus `make gate` | that the managed profile works: that needs a project and lives in `tests/integration/` |
| Consequential decisions are deterministic and replayable | step 2, step 3, `make gate` | that a model's narration is deterministic; it is not, and it never decides |
| Escalations reach a human | step 3, step 5 | that a reviewer acted; the queue shows submitted, not reviewed |
| The audit record is tamper-evident and portable | step 6, step 7, `make portability` | tamper-PROOF: file access beats any store |
| Every port is swappable and every seam is named | step 8, `make portability` | that an on-premises deployment exists, or model or infrastructure portability |

## The UI

```bash
make ui-install && make ui-dev     # http://localhost:3000, proxying to the service
```

Worth showing only if the audience cares about embedding. The point is not the screen: it is that
the browser never asserts who the user is, the service credential never leaves the server, and
framing and CORS are per-tenant allowlists that refuse a wildcard. See `ui/README.md`.

## Managed profile (gcp)

Set `CONTACT_PROFILE=gcp` and install the `[gcp]` extra; identity becomes
the platform's signed assertion and audit becomes the Cloud Logging WORM sink. This is NOT part
of the offline demo and needs a real project. See `docs/runbook.md`.
