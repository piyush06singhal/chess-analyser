# Caissa — web app

The Next.js frontend for Caissa. It is a client of the API in `apps/api` and
holds no chess logic: every number it shows comes from the backend, and it
computes no analysis of its own.

## Develop

From the repository root, `docker compose up` serves the whole stack (web on
<http://localhost:3100>, API on <http://localhost:8002>). To run the web app on
its own against a running API:

```bash
cd apps/web
npm install
NEXT_PUBLIC_API_URL=http://localhost:8002 npm run dev   # http://localhost:3000
```

`NEXT_PUBLIC_API_URL` is baked in at build time; it must match the variable read
by `src/lib/api.ts`.

## Checks

```bash
npm run lint    # eslint, 0 warnings expected
npm run build   # type-check + production build
npx playwright test tests/e2e/accessibility.spec.ts   # axe, serious/critical = 0
npx playwright test tests/e2e/responsive.spec.ts      # no overflow at 390/768/1280 px
```

The browser suites run against a live app; set `ARGUS_WEB_URL` to point at one.

See the repository root [`README.md`](../../README.md) and
[`docs/product/`](../../docs/product/) for the full picture.
