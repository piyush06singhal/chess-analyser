# Caissa — Security Report

**Release:** 1.0.0 (release candidate `v1.0.0-rc.1`)
**Date:** 2026-10-04

Every result below is a command that was run against the release candidate. Where
a check could not be run (a tool is not installed), that is stated as `NOT RUN`
rather than assumed clean.

## Dependency vulnerabilities

All three checks are reproducible with one command:
`python scripts/audit_dependencies.py` (a skipped tool prints `SKIPPED`, never a
fake pass).

| Scan | Command | Result |
| --- | --- | --- |
| Python deps | `python -m pip_audit` | **0 known vulnerabilities** (74 installed packages) |
| Frontend production deps | `npm audit --omit=dev` | **0 vulnerabilities** |
| Frontend full tree | `npm audit` | 5 high — all in the ESLint dev toolchain (`eslint-config-next` → `@next/eslint-plugin-next` → `fast-glob` → `micromatch` → `braces`); not shipped in the runtime image |

The CI `security` job enforces the same split as the table above: the shipped
tree (`npm audit --omit=dev`) **blocks** the build, and the full tree is printed
by a separate non-blocking step so an unpatchable dev finding stays in the record
and a patched one becomes obvious the moment a fix is published.

**`braces` has no patch to apply.** GHSA-vfj7-8cjw-p6xm (CVE-2026-93687) lists
affected versions `<= 3.0.3` and patched versions **none**; 3.0.3 is the newest
release on the registry. It is a stack-exhaustion DoS that needs a deeply nested
brace pattern supplied to a glob, and the only path to it here is
`eslint-config-next` while linting, from developer-authored patterns — not from
user input, and not from the runtime image at all. Recorded as **accepted
residual risk**, and handled the same way Trivy's `ignore-unfixed` handles the
container findings below.
| Container images | `docker scout cves local://<image>` | web **0 critical / 0 high**; api **0 critical / 3 high** — see below |

### Container findings (measured, not estimated)

The web image is clean after hardening: the runtime stage copied only the
standalone output but still inherited the base image's bundled **npm**, whose own
dependency tree (`pacote`, `sigstore`, `brace-expansion`, `picomatch`,
`http-cache-semantics`, `ip-address`) carried **11 HIGH** advisories. The runtime
does not use npm, so it is now removed from the image — the web image scans at
**0 critical / 0 high**. Before/after: `argus-chess-web` 11 HIGH → **0**.

The api image started at 4 HIGH. Adding `apt-get upgrade` to the build pulled the
patched `libpcre2` and cleared one, leaving **3 HIGH, 0 critical**:

| Package | CVEs | Status |
| --- | --- | --- |
| `gcc-14-base` | CVE-2026-95619, CVE-2026-102010 | metadata-only package (no compiler, no binaries); no upstream fix |
| `zlib1g` | CVE-2026-85091 | runtime library; **no fix published** by Debian 13 at this base |

Docker Scout reports the base image as already up to date and shows the same
HIGH count on the refreshed base, so these cannot be removed by a base bump
today. The mitigations that apply are structural and already in place: the
process runs as unprivileged uid 10001, the base is pinned by digest, no build
toolchain is installed, and the packages are not reachable from the application
surface. They are recorded here as **accepted residual risk**, not as a pass.

### Advisory fixed this phase

`next` 16.2.0 – 16.3.5 is affected by **GHSA-vcvr-r3jv-pc5j** (critical: RCE in the
Node.js `next/og` `ImageResponse` when attacker-controlled values reach SVG
content). The release candidate pinned `next@16.3.5`. It was **upgraded to
`next@16.3.8`** (patched; `eslint-config-next` matched), and the frontend was
rebuilt, re-linted, type-checked and re-tested.

- **Exploitability in this app:** the vulnerability requires the application to
  pass attacker-controlled values into `next/og`'s Node `ImageResponse`. Caissa uses
  **no** `next/og` / `ImageResponse` code path (verified: no `ImageResponse`, no
  `opengraph-image`/`twitter-image` route). It was therefore not exploitable here,
  but the advisory is resolved for the release all the same.

## Secret handling

| Check | Result |
| --- | --- |
| Committed secrets (`sk-…`, `gsk_…`, AWS keys, PEM private keys, inline `api_key=…`) | none found across `*.py/.ts/.tsx/.yml/.yaml/.json/.md` |
| `.env` tracked by git | **no**; `.env` is git-ignored |
| LLM key exposure | read from environment only; a redaction pass scrubs traces; `describe()` never emits the key (covered by `test_llm_coach.py::test_describe_never_leaks_key`) |
| Provider error surfaced to a client | fixed this phase — the legacy `/chat` shell returned a raw provider 502 body; it now answers honestly instead |

## Application-security tests

The full suite includes dedicated security modules, run this phase:

| Module | Focus |
| --- | --- |
| `tests/test_security.py` | auth, rate limiting, prompt-injection payloads in headers/body |
| `tests/test_phase15_api_hardening.py` | API-key enforcement, resource gate, CORS |
| `tests/test_phase15_hardening.py` | adversarial/hardening cases |
| `tests/test_phase15_privacy.py` | cross-caller isolation (a caller cannot read another's game) |

Result: **181 passed** across the agent/security modules; **1584 passed, 1 skipped**
for the whole suite.

## Container / image posture

- Production compose runs non-root, publishes no database port, injects secrets
  from the environment (no committed file), and applies CPU/memory limits.
- Base images are pinned by digest; the web runtime image carries no npm.
- A container image CVE scan **was run** this phase (`docker scout cves`); the
  results and their disposition are in *Container findings* above.
- No SBOM is produced, and no image signing/attestation is configured.

## Verdict

No committed secret, **0 known vulnerabilities** in the Python environment and in
the frontend production dependencies, and the one critical `next` advisory was
patched. The container images carry **0 critical** findings; the residual HIGH
items are base-OS packages with no upstream fix, dispositioned above as accepted
risk. No check is reported as clean that was not measured.
