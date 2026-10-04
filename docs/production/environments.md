# Environments

Caissa recognises four environments, selected by `ARGUS_ENV`. Each has its own
template (`.env.<env>.example`) and its own rules, enforced by
`argus config validate`.

| Environment | Purpose | Template |
| --- | --- | --- |
| `development` | Local work. Permissive, fast, no auth. | `.env.development.example` |
| `test` | A test-shaped stack: isolated DB, no external services. | `.env.test.example` |
| `staging` | Production-shaped, isolated data, its own secrets. | `.env.staging.example` |
| `production` | The real service. Strict rules apply. | `.env.production.example` |

## What differs

| Setting | development | test | staging | production |
| --- | --- | --- | --- | --- |
| API keys | open (empty) | open | required | **required** |
| Database | PostgreSQL or SQLite | isolated PostgreSQL | PostgreSQL | **PostgreSQL only** |
| Log format | text | text | json | **json** |
| LLM provider | empty/echo | empty | real | **real (not echo)** |
| Engine depth | 12 | 6 | 14 | 16 |
| CORS origins | localhost | localhost | staging origin | **exact prod origin** |

## Why the production rules exist

They are not bureaucracy; each one prevents a specific class of incident:

* **API keys required** — an open production deployment has no authentication,
  so every request is the `local` caller and every game is visible to everyone.
* **PostgreSQL required** — SQLite is a single-writer file, fine for tests and
  local work, wrong for concurrent production traffic.
* **JSON logs** — text logs cannot be aggregated or queried, which makes an
  incident harder to reconstruct exactly when it matters.
* **Not the `echo` provider** — `echo` is a self-identifying development stub
  that performs no external calls; shipping it would make the coach look broken
  while pretending to work.

## Staging must mirror production

Staging runs the release candidate, the migration dry run, and the load and
smoke tests before production does. It differs from production only in scale,
data and secrets — never in shape. If a change passes in staging but fails in
production, that is a gap in staging's configuration, and it should be closed
there rather than worked around.

## Selecting the environment

`ARGUS_ENV` is read from the process environment (a secrets manager in
production, a `.env` file locally). The compose files set it explicitly. The
value is reported by `GET /health` as `environment`, so a running service always
states which environment it believes it is.
