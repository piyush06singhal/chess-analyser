"""Caissa core library.

Packages:
- ``argus.shared``      — cross-cutting errors and logging
- ``argus.chess_core``  — pure chess data handling (FEN, PGN, moves, positions)
- ``argus.analysis``    — engine integration, features, phases, classification,
                          game analysis and reports (deterministic only)
- ``argus.ai_agent``    — AI coaching agent foundation (tool abstraction layer)
- ``argus.ml``          — ML scaffolding (datasets, validation, pipelines)

Design rule: deterministic chess analysis (engine + features) is kept strictly
separate from probabilistic AI functionality (LLM/ML).
"""

#: The Caissa release version of the core library, kept in lockstep with the
#: repository ``VERSION`` file. Subsystem methodology versions (analysis,
#: player profile, graph, …) are separate and version what a *symbol means*.
__version__ = "1.0.0"
