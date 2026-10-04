# Security (production)

The security model, the boundaries enforced in code, and what is not yet built.
This complements `docs/security.md` (the application-level model) with the
deployment-level controls.

## Authentication

API-key authentication in `argus_api.security`:

* **Keyed mode.** When `ARGUS_API_KEYS` is set (format `key:caller:role`), every
  request must present a valid key as `X-API-Key` or `Authorization: Bearer`.
  Comparison is constant-time (`hmac.compare_digest`) against every configured
  key, so a wrong key cannot be distinguished by timing.
* **Open mode.** When it is empty, the deployment is explicitly **open**: every
  request is the `local` caller. This is fine for local development and is
  **refused in production** by `validate_settings`.
* **Fail closed.** Authentication is resolved in `RequestContextMiddleware`
  before any route runs, so a request that fails authentication never reaches a
  handler and gets a 401 with a request id.
* **CORS preflight** carries no credentials by design, so `OPTIONS` is not
  authenticated — the `CORSMiddleware` must be free to answer it. The real
  request that follows is authenticated normally.

`GET /ready` reports `auth.mode` (`open`/`keyed`) as a measured fact.

## Security headers

`SecurityHeadersMiddleware` sets, on every response:

* `X-Content-Type-Options: nosniff`
* `X-Frame-Options: DENY`
* `Referrer-Policy: no-referrer`
* `Cross-Origin-Opener-Policy: same-origin`
* `Cross-Origin-Resource-Policy: same-site`
* `Permissions-Policy: geolocation=(), microphone=(), camera=()`
* `Content-Security-Policy: default-src 'none'; frame-ancestors 'none'; base-uri 'none'`
* `Strict-Transport-Security: max-age=31536000; includeSubDomains`

The CSP is deliberately conservative for a JSON API (it returns data, not
documents). The web app sets its own CSP in `next.config`.

## Upload security

`argus_api.services.uploads` validates extension, MIME type, size and UTF-8
validity, sanitizes the displayed filename against traversal, and treats the
content as data only. Uploads are bounded by size, not scanned for content — a
content scanner is a separate service and is not present.

## Data privacy

* **Export and delete.** A game and everything that cascades from it can be
  deleted (`DELETE /api/games/{id}`); the cascade removes moves, analyses,
  critical positions and training provenance. An operator can export a game's
  stored PGN (`GET /api/live/{id}/pgn` for live games; game detail for stored
  ones).
* **No third-party data.** Chess.com and Lichess are read key-less by username;
  nothing is stored until a specific game is imported. No credentials exist.
* **Logs hold no content.** `/api/live/metrics` records counts only, never moves,
  positions or identities. Audit events carry identifiers, never game content.

## Audit logging

`argus_api.observability.audit` emits structured, JSON-encoded security-relevant
events under the `argus.audit` logger, so an operator can route them to
tamper-resistant storage. Each event carries the request id and caller id, never
a credential.

## Supply chain

* The CI `security` job runs `pip-audit` (Python), `npm audit --omit=dev`
  (the frontend tree that ships) and Trivy (filesystem: vulnerabilities, secrets,
  misconfiguration), failing on HIGH/CRITICAL. The frontend's full tree is
  reported by a non-blocking step as well, because a dev-only advisory with no
  published patch should be visible without stopping a release; the reason it is
  not gating is recorded in
  [../release/security-report.md](../release/security-report.md).
* Container base images are pinned by digest (see
  [containerization.md](containerization.md)).
* Secrets are environment-only; no key is hardcoded, and the secret scanner fails
  the build on a committed credential.

## What is not here yet (stated, not hidden)

* **No user accounts.** API keys identify a caller, but the allowed game set is
  currently the whole library (single-user). The authorization seam
  (`argus_api.services.authorization`) is fail-closed and tested; it changes in
  one place when accounts arrive.
* **No CSRF surface.** There is no cookie-based auth, so there is nothing to
  forge; this changes with accounts.
* **The rate limiter is per process.** See [reliability-model.md](reliability-model.md).
* **Uploads are not content-scanned.**

Each of these is a real, known boundary — not a claim of completeness.