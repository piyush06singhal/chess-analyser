# `evaluation/` — Phase 14 outputs

This directory holds the **generated** evaluation artifacts. The evaluation code
itself lives in the Python package so it ships with the library and is importable
in tests and CI.

| Suggested layout (spec §3) | Where it actually lives |
| --- | --- |
| `evaluation/datasets/` | declared in code: `argus/evaluation/datasets.py` (a descriptor registry, not loose files) |
| `evaluation/fixtures/` | `argus/evaluation/fixtures.py` (versioned, in code so it is type-checked) |
| `evaluation/benchmarks/` | `argus/evaluation/suites/` |
| `evaluation/engine/`, `intelligence/`, `ml/`, `agent/`, `training/`, `opponent/`, `realtime/`, `graph/` | one module per subsystem in `argus/evaluation/suites/` |
| `evaluation/reports/` | here — `run_evaluation.py` writes `argus-evaluation.{json,txt}` |
| `evaluation/scripts/` | `scripts/run_evaluation.py`, `scripts/benchmark_api.py`, `scripts/quality_check.py` |

Keeping the fixtures and datasets **in code** rather than as loose files means
they are typed, imported by the suites directly, and cannot silently drift from
the checks that consume them. Generated reports stay here because they are output,
not source.

## Running

```bash
python scripts/run_evaluation.py                 # full certification run
python scripts/run_evaluation.py --no-engine     # offline; engine suite skipped
python scripts/quality_check.py                  # the inner-loop quality command
python scripts/benchmark_api.py                  # API latency (needs a running stack)
```
