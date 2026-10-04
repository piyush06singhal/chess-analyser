import { expect, test } from "@playwright/test";

import { expectNoSeriousViolations } from "./a11y";

// The healthy case is covered by the accessibility scan of /system. This covers
// the case that matters for an operator view: when something is down, does the
// page say so plainly, or does it smooth the failure into a green badge? The API
// responses are intercepted so the *page's* honesty is what is under test, with
// no dependency on stopping the real engine or Redis.

const DEGRADED_READY = {
  status: "degraded",
  service: "argus-api",
  version: "1.0.0",
  environment: "test",
  blocking: ["stockfish"],
  checks: {
    database: { ok: true, detail: { configured: true, connected: true, dialect: "postgresql" } },
    stockfish: {
      ok: false,
      name: "stockfish",
      version: null,
      detail: { available: false, reason: "binary not found at /usr/games/stockfish" },
    },
    migrations: {
      ok: true,
      expected: 15,
      present: 15,
      missing: [],
      detail: "All expected tables are present.",
    },
    redis: {
      ok: false,
      detail: { configured: true, connected: false, reason: "connection refused" },
    },
    ml_registry: {
      ok: true,
      registered: 0,
      production_models: [],
      detail: "No model has passed its production gate, so predictions are unavailable by design.",
    },
    ai_provider: {
      ok: true,
      configured: false,
      provider: null,
      detail: "No LLM provider configured: the coach returns an explicit 501 and every deterministic path still works.",
    },
  },
  checked_at: "2026-01-01T00:00:00Z",
};

const INCONSISTENT_GRAPH = {
  healthy: false,
  schema_version: "13.0",
  report: {
    node_count: 3,
    edge_count: 1,
    orphan_nodes: { count: 1 },
    dangling_edges: { count: 2 },
    invalid_edges: { count: 0 },
    missing_evidence: { count: 1 },
    stale_edges: { count: 0 },
    duplicate_edges: { count: 0 },
  },
};

test("System Health reports degradation honestly", async ({ page }) => {
  await page.route("**/ready", (route) => route.fulfill({ json: DEGRADED_READY }));
  await page.route("**/api/graph/health", (route) => route.fulfill({ json: INCONSISTENT_GRAPH }));

  await page.goto("/system");
  await page.waitForLoadState("load");

  await expect(page.getByText("Degraded", { exact: true })).toBeVisible();
  await expect(page.getByText("Blocking: stockfish")).toBeVisible();
  // A failing dependency states the real reason, not a status word.
  await expect(page.getByText("binary not found at /usr/games/stockfish")).toBeVisible();
  await expect(page.getByText("connection refused")).toBeVisible();
  // The graph's own counts are shown, and inconsistency is named.
  await expect(page.getByText("Inconsistent", { exact: true })).toBeVisible();
  await expect(page.getByText("Dangling edges", { exact: true })).toBeVisible();

  await expectNoSeriousViolations(page, "System Health (degraded)");
});
