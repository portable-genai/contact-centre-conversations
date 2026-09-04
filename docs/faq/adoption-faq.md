# FAQ: adopting and forking this repo

For the engineering lead who has been handed this repository. The long form is
[`../ADOPTING.md`](../ADOPTING.md); this page answers the questions that come first.

## What is the very first decision?

Which modes you run. Both `agent_assist` and `self_service` default OFF, and turning on the
second one is a different conversation from turning on the first: `self_service` reaches a
member of the public with drafted prose, `agent_assist` puts a trained human between the model
and the customer. Each has its own `model-quality-gate` promotion bundle
(`CONTACT_AGENT_ASSIST_BUNDLE`, `CONTACT_SELF_SERVICE_BUNDLE`), its own rubric set and its own
sign-off. A mode enabled with no bundle refuses to boot under any profile except a deliberate
offline `local`.

The flags are read in three states: unset is off, deliberately EMPTIED refuses to boot rather
than inheriting that default, and an unrecognised or mis-capitalised value refuses too.
`tests/unit/test_mode_gating.py` is the gate.

## How much of this do we have to change?

Most adoptions change `config/packs/` and nothing else. The six engines know no market and no
wording; the packs carry all of it. If your contact model genuinely differs (a procedure shape
this one cannot express, a decision none of the six engines makes) you are into `domain/`, and
that is a bigger conversation than an adoption.

## How do we rebrand it?

`scripts/rename_fork.py`, in one pass, preview first:

```bash
python scripts/rename_fork.py --package acme_contact_centre \
    --env-prefix ACMECONTACT --resource acme-contact-centre \
    --name-prefix acme-contact --dry-run
```

It rewrites the package name, the `CONTACT` environment prefix, the distribution and resource id
and the Terraform `name_prefix` default, then renames `src/contact_centre_conversations/`. It writes nothing
without `--yes`. There is no `--cli` flag because `[project.scripts]` names the console script
after the package, and no `--dist` flag because the distribution name, the GitHub id and the A2A
agent-card name are the same one literal that `--resource` renames.

It does NOT rename the two `model-quality-gate` promotion bundle ids, on purpose: they are deployment values, and
a promotion record that silently changed identity would be worse than one you had to set by hand.

Recreate the venv afterwards: the distribution name changed, so an existing editable install
points at a package that no longer exists.

## Which files will conflict when we pull upstream fixes?

[`../ADOPTING.md`](../ADOPTING.md) section 2 has the list. The short version: take our changes to
`domain/kernel.py`, `domain/contact_kernel.py`, the six engines, `domain/guardrails.py`,
`domain/suggestions.py`, `ports/`, `tests/contract/`, `eval/run_eval.py` and the CI workflows;
expect to own everything under `config/packs/`, the corpus and streams, `adapters/onprem/*`, UI
theming, both golden eval datasets and the regulator crosswalk in `COMPLIANCE.md`. Track upstream
by tag and rebase rather than merging `main` continuously.

## What are the real extension points?

- **A new market, procedure, disclosure, intent or action**: a pack under `config/packs/`. No
  code.
- **A new adapter**: the class under `adapters/<family>/`, the same `module:Class` target in
  `config.DEFAULT_BINDINGS` AND `config/settings.yaml`, and any new variable in `.env.example`.
  `tests/unit/test_settings_file.py` fails if the two binding tables disagree.
- **A new port**: five places, or it runs with no enforcement at all. `ports/__init__.py`
  (`PORT_PROTOCOLS`), `config.DEFAULT_BINDINGS`, a `Container` accessor, `config/settings.yaml`,
  and a `PortCase` in `tests/contract/canonical.py`, then bound in all three families.
  `tests/contract/test_port_parity.py` asserts set equality across the five.
- **A new agent tool**: the callable plus its skill in `agent/agent_card.py`. The card and the
  tool table are compared for set equality.

[`../../CONTRIBUTING.md`](../../CONTRIBUTING.md) carries the file-by-file walkthrough with the
test that enforces each row.

## How do we know a change did not break anything?

```sh
make gate            # ruff, ruff format, mypy strict, the suite except integration, both evals
make tf-check        # terraform validate, fmt and test against a MOCKED provider
make demo-selftest   # the whole demo arc, headless, asserting every narrated claim
make portability     # the exit tour, pass or fail per named check
make docs-check      # relative links, code fences, no em-dash in shipped prose
make audit           # pip-audit over both lockfiles (the one step that needs the network)
```

`make gate` is deliberately offline, credential-free and network-free. If a change makes the gate
need a cloud project, the change is wrong, not the gate.

## What do the evals actually measure, and can we trust them?

Two rubric sets, one per mode:

- **agent_assist**: `next_step_accuracy`, `reminder_timeliness`, `citation_accuracy`,
  `groundedness`, `pii_safety`.
- **self_service**: `gate_precision`, `handoff_safety`, `maker_checker_safety`, `containment`.

Run one on its own with `python eval/run_eval.py --rubric self_service`. Every metric is proved
able to fail: `tests/unit/test_eval_falsification.py` and `tests/unit/test_not_falsely_green.py`
plant a mutant and fail the build if the metric still passes.

`containment` is the one with a deliberately modest threshold, because a self-service assistant
that contains too much is a worse outcome than one that hands off. Choose your own number with
your conduct function; do not raise it because a dashboard looks better.

And the corollary for a fork: you inherit a green gate that measures the WRONG policy until you
rebuild both golden sets for your packs.

## What do we have to supply that is not in the repo?

1. **Policy packs**, signed off by conduct. Everything else is downstream of them.
2. **A knowledge corpus**, governed, reachable through `enterprise-knowledge-base` or supplied as a fixture. Its quality
   IS the quality of the suggestions.
3. **An `agent-guardrail-gateway`** at `guardrail_url`, and a decision about what an unavailable screen costs
   per mode.
4. **An IdP**, configured on the deployed service, plus `CONTACT_IAP_AUDIENCE`.
5. **An `human-review-console` endpoint**, or nothing consequential reaches a human.
6. **A promotion bundle per mode**, or the mode refuses to boot outside `local`.

## What is still open?

[`../practices-audit.md`](../practices-audit.md) carries the honest per-check verdict, and it
says one thing worth repeating here: this repo has not entered the cross-repo practices-audit
matrix, so every row in it is a self-assessment against the tree rather than an independent
review. The items that need your network and your project rather than a code change are the `agent-observability` binding, registering both bundles with `model-quality-gate`, and the private-egress rule. The
Terraform in this repo is validated and tested offline and has never been applied.
