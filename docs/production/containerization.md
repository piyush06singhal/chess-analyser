# Containerization

Two images, built from `docker/Dockerfile.api` and `docker/Dockerfile.web`, plus
a hardened production compose file (`docker-compose.production.yml`).

## Hardening

Both Dockerfiles are production-hardened:

* **Base images pinned by digest.** `python:3.12-slim` and `node:22-alpine` are
  referenced by their multi-arch index digest, so a rebuild cannot silently pull
  a different toolchain. Resolve the current digest with
  `docker buildx imagetools inspect python:3.12-slim`.
* **Non-root runtime.** The API runs as `argus` (uid 10001); the web app runs as
  the image's unprivileged `node` user. A container escape does not start as
  root.
* **Minimal runtime.** The web image ships only the Next.js standalone output;
  the API image installs only what the wheels and Stockfish need, and clears
  `apt` lists.
* **Real healthchecks.** The API image hits `/health`; compose healthchecks gate
  startup order so the API does not start before Postgres is accepting
  connections.

## Production compose

```bash
# Required secrets/vars:
export POSTGRES_PASSWORD=... REDIS_PASSWORD=...
export ARGUS_CORS_ORIGINS=https://argus.example
export ARGUS_API_KEYS='<key>:operator:operator'
export NEXT_PUBLIC_API_URL=https://api.argus.example

docker compose -f docker-compose.production.yml up -d
```

Differences from the development compose, on purpose:

* **No source bind-mounts.** The image is the artifact, not the working tree. A
  running production service must not change because someone edited a file.
* **PostgreSQL is not published on a host port.** It is reachable only on the
  compose network.
* **Redis requires a password** and runs with a bounded memory + LRU eviction.
* **CPU/memory limits** on every service, so one runaway analysis cannot take
  the host down.
* **`restart: always`** and healthchecks.

## Resource limits

| Service | CPU | Memory |
| --- | --- | --- |
| api | 2.0 | 2g |
| web | 1.0 | 512m |
| postgres | 2.0 | 2g |
| redis | 1.0 | 768m |

These are starting points sized for a small deployment. The engine gate
(`ARGUS_ENGINE_MAX_CONCURRENCY`) is the per-process bound that matters more:
Stockfish is CPU-bound, and the gate is what stops a burst of analyses from
oversubscribing the container.

## Verifying an image

```bash
docker build -f docker/Dockerfile.api -t argus-api:local .
docker run --rm argus-api:local python -c "import argus_api, argus; print('ok')"
```

The CI `security` job scans the filesystem (including Dockerfiles) with Trivy for
vulnerabilities, secrets and misconfiguration on every push.
