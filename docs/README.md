# Caissa documentation

This is the entry point for the documentation. It is organised by **what you are
trying to do**, not by which module owns the code, so a reader can find the right
document without knowing the layout of the repository.

The product rule the documents follow is the code's own rule: a capability that
is not built is reported as not built, and a number that was not measured is not
shown. Where a document describes something not yet done, it says so and points
at [`release/DEFERRED_WORK.md`](release/DEFERRED_WORK.md).

## Start here

| Document | Read it to |
| --- | --- |
| [`system-overview.md`](system-overview.md) | get the whole system in one page — components, data flow, and where authority lives |
| [`architecture.md`](architecture.md) | understand the internal layering and the boundaries between packages |
| [`development.md`](development.md) | set up the project, run the tests, and follow the conventions |
| [`deployment.md`](deployment.md) | run Caissa, configure it, and check whether a deployment is healthy |
| [`security.md`](security.md) | see the security and privacy model and the authorization rule |

## Features

| Document | Covers |
| --- | --- |
| [`chess-analysis.md`](chess-analysis.md) | engine evaluation, move classification, the analysis lifecycle |
| [`game-intelligence.md`](game-intelligence.md) | the per-game report: phases, opening, material, tactics, accuracy, turning points |
| [`player-intelligence.md`](player-intelligence.md) | the versioned player profile and Chess DNA |
| [`training-engine.md`](training-engine.md) | exercises generated from your own mistakes, spaced repetition |
| [`opponent-intelligence.md`](opponent-intelligence.md) | repertoire, tendencies and preparation from an opponent's games |
| [`coaching.md`](coaching.md) | the AI coach and its evidence-grounded answer model |
| [`live-chess.md`](live-chess.md) | server-authoritative live play, clocks and fair play |
| [`realtime-protocol.md`](realtime-protocol.md) | the live event protocol and its recovery path |
| [`scenarios.md`](scenarios.md) | what-if comparison and counterfactual analysis |
| [`ai-agent.md`](ai-agent.md) | the agent design: tools, claims, evidence and validation |
| [`ml-and-data.md`](ml-and-data.md) | the data pipeline and ML design — deliberately deferred, see the deferred-work register |

## One section, many files

| Section | Read it to |
| --- | --- |
| [`intelligence-graph/`](intelligence-graph/README.md) | understand the evidence graph — nodes, relationships, queries and the map |
| [`evaluation/`](evaluation/README.md) | see how each layer is measured and verified |
| [`production/`](production/README.md) | operate it: environments, migrations, reliability, runbooks, backup |
| [`product/`](product/design-system.md) | see the design system, accessibility and user journeys |
| [`release/`](release/RELEASE_NOTES.md) | read release notes, the sign-off and blockers ([`RELEASE_STATUS.md`](release/RELEASE_STATUS.md)), and what is deferred |

## Repository-level documents

- [`../README.md`](../README.md) — project overview and quickstart
- [`../CHANGELOG.md`](../CHANGELOG.md) — what changed, per release
- [`../PRODUCTION_READINESS_REPORT.md`](../PRODUCTION_READINESS_REPORT.md) — the readiness assessment behind the release decision

## Conventions

Every document states its own scope and stays honest about gaps. Verification
claims are reproducible from `scripts/` — for example `scripts/system_check.py`
reports a deployment's real state, and `scripts/run_evaluation.py` runs the
release gate. A number in these documents is only as good as the command that
produced it.
