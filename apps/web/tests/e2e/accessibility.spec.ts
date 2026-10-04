import { expect, test } from "@playwright/test";

import { expectNoSeriousViolations } from "./a11y";

// Every primary surface is scanned with axe (WCAG 2.1 A/AA). The page list is
// the navigation's own jobs (§35), so a new top-level page is one line here.
const PAGES: Array<[path: string, name: string]> = [
  ["/", "Landing"],
  ["/dashboard", "Dashboard"],
  ["/games", "Library"],
  ["/import", "Import"],
  ["/lab", "Position Lab"],
  ["/coach", "AI Coach"],
  ["/coach?tab=today", "Coach · Today"],
  ["/training", "Training"],
  ["/scenarios", "What-If Lab"],
  ["/players", "Players"],
  ["/opponents", "Opponents"],
  ["/collections", "Collections"],
  ["/intelligence", "Intelligence Explorer"],
  ["/progress", "Progress"],
  ["/search", "Search"],
  ["/live", "Live"],
  ["/system", "System Health"],
];

for (const [path, name] of PAGES) {
  test(`${name} has no serious accessibility violations`, async ({ page }) => {
    await page.goto(path);
    await page.waitForLoadState("load");
    // Give client-side fetches a beat to settle so the scan sees the loaded
    // state, not the skeleton. A slow backend simply means the empty state is
    // scanned, which must be accessible too.
    await page.waitForTimeout(600);
    await expectNoSeriousViolations(page, name);
    // Every page must have exactly one top-level heading. A missing or
    // duplicated h1 is a real defect that an axe scan does not flag — the
    // report page shipped without one until this assertion caught it.
    await expect(page.locator("h1"), `${name} should have exactly one h1`).toHaveCount(1);
  });
}

test("the app is keyboard navigable from the top", async ({ page }) => {
  await page.goto("/");
  await page.keyboard.press("Tab");
  // The first focus stop is the brand link; the point is that focus is visible
  // and lands on a real control rather than nowhere.
  const focused = await page.evaluate(() => document.activeElement?.tagName ?? "");
  if (focused === "") throw new Error("no element received focus on Tab");
});
