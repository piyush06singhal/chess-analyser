import { expect, test } from "@playwright/test";

import { expectNoSeriousViolations } from "./a11y";

const API_URL = process.env.ARGUS_API_URL ?? "http://localhost:8002";

// The report is the product's centre of gravity, so it is the one data-dependent
// page the suite insists on. When the API is down or the library is empty the
// test skips with a reason rather than failing — a missing fixture is not a
// frontend defect.
test("a real game report renders and is accessible", async ({ page, request }) => {
  let gameId: string | null = null;
  try {
    const response = await request.get(`${API_URL}/api/games`);
    if (response.ok()) {
      const body = (await response.json()) as { games?: Array<Record<string, unknown>> };
      const analyzed = (body.games ?? []).find((game) =>
        ["ready", "analyzed"].includes(String(game.analysis_status)),
      );
      gameId = (analyzed?.id as string | undefined) ?? null;
    }
  } catch {
    gameId = null;
  }
  test.skip(!gameId, "no analysed game is available (API not running or library empty)");

  await page.goto(`/game/${gameId}/report`);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await page.waitForTimeout(800);
  await expectNoSeriousViolations(page, "game report");
});
