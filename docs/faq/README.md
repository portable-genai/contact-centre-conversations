# FAQ index

Answers to the questions different teams ask when evaluating, adopting or reviewing this
repository as a common base for contact-centre conversational AI. Each page is written for one
audience; skim the one that matches your role.

| FAQ | For | Answers |
|---|---|---|
| [security-faq.md](security-faq.md) | AppSec and security review | server-side identity, the screening order, what reaches a model, secrets, supply chain, the audit chain, what is in and out of scope |
| [portability-faq.md](portability-faq.md) | architecture, cloud, exit planning | the three profiles, the thirteen ports, the no-lock-in claim, the on-premises exit, data export |
| [features-faq.md](features-faq.md) | product, contact-centre operations | what the two modes do, what is deterministic and what is not, and the boundary with sibling systems |
| [adoption-faq.md](adoption-faq.md) | engineering leads forking the repo | the rename, upstream fixes, the policy packs as the real extension point, the mode gating |
| [compliance-faq.md](compliance-faq.md) | compliance, conduct, privacy, model risk | regulatory posture, evidence, maker-checker, residency, model-risk evidence |

Everything here turns on one fact worth stating up front: this repo serves TWO separately gated
modes with different risk postures. `agent-assist` whispers to a trained human who decides;
`self-service` reaches a member of the public directly. Both default off. Where a control differs
between them, these pages say so rather than averaging them.

These pages deliberately do NOT re-document capabilities owned by sibling systems in the catalog.
Where a concern belongs to another system (the guardrail gateway `agent-guardrail-gateway`, the knowledge base `enterprise-knowledge-base`,
the agent registry `agent-registry`, the AI-quality gate `model-quality-gate`, the observability and WORM audit sink `agent-observability`,
the human-review console `human-review-console`, the post-contact QA scorecard E3), the FAQ points at it and
explains the boundary rather than duplicating it. See [features-faq.md](features-faq.md) for the
full map.

Authority order for anything these pages disagree with: `SPEC.md`, then `ARCHITECTURE.md`, then
`COMPLIANCE.md`, then `README.md`. These pages restate; they do not decide.
