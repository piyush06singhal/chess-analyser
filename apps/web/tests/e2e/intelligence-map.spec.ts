import { expect, test, type APIRequestContext } from "@playwright/test";

import { expectNoSeriousViolations } from "./a11y";

const API_URL = process.env.ARGUS_API_URL ?? "http://localhost:8002";

/**
 * Find a stored node whose neighbourhood holds more than the node itself.
 *
 * The map draws a graphic only when the library holds real relationships. With an
 * empty library the product correctly shows a "nothing is connected" note instead
 * of an empty canvas, so there is no `svg[role="img"]` to find — a missing fixture
 * is not a frontend defect (the same rule the report suite follows). The browser job
 * seeds the API through `verify_journeys.py` before this suite, so the graphic is
 * normally present; the skip is the honest fallback when it is not.
 */
async function findDrawableNode(
  request: APIRequestContext,
): Promise<{ type: string; key: string } | null> {
  for (const type of ["player", "game", "position", "opening"]) {
    try {
      const listing = await request.get(`${API_URL}/api/graph/nodes/${type}?limit=25`);
      if (!listing.ok()) continue;
      const body = (await listing.json()) as { nodes?: Array<{ node_key: string }> };
      for (const candidate of body.nodes ?? []) {
        const response = await request.get(
          `${API_URL}/api/graph/neighborhood/${type}/${encodeURIComponent(
            candidate.node_key,
          )}?depth=2&limit=120`,
        );
        if (!response.ok()) continue;
        const graph = (await response.json()) as { found?: boolean; nodes?: unknown[] };
        if (graph.found && (graph.nodes?.length ?? 0) > 1) {
          return { type, key: candidate.node_key };
        }
      }
    } catch {
      // The API is unreachable, which is also just an absent fixture.
    }
  }
  return null;
}

test("the map deep-link renders a neighbourhood from the URL", async ({ page, request }) => {
  // The URL is the state, and that holds with or without stored data.
  await page.goto("/intelligence?tab=map&depth=2");
  await expect(page.getByRole("button", { name: "Map", exact: true })).toHaveAttribute(
    "aria-current",
    "true",
  );
  await expect(page.locator(".segmented").getByRole("button", { name: "2" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );

  const node = await findDrawableNode(request);
  if (!node) {
    test.skip(true, "the library holds no graph neighbourhood to draw");
    return;
  }

  // A real deep link: the node and the depth both come from the URL, not from the
  // picker's default, which is what the deep-link claim actually means.
  await page.goto(
    `/intelligence?tab=map&type=${node.type}&key=${encodeURIComponent(node.key)}&depth=2`,
  );
  const map = page.locator("svg[role='img']");
  await expect(map).toBeVisible({ timeout: 15_000 });
  await expect(map).toHaveAttribute("aria-label", /nodes and \d+ relationships/);

  await expectNoSeriousViolations(page, "intelligence map");
});

test("changing depth writes the URL rather than a second copy of the state", async ({ page }) => {
  await page.goto("/intelligence?tab=map&depth=1");
  const three = page.locator(".segmented").getByRole("button", { name: "3" });

  // The click is retried until it lands. The button is present and actionable in
  // the server-rendered HTML before React has hydrated, so a click delivered in
  // that window is silently dropped — the failure mode is a dead click, not a
  // wrong result. `toPass` re-clicks until the pressed state actually changes,
  // which makes the test assert the behaviour rather than the hydration timing.
  await expect(async () => {
    await three.click();
    await expect(three).toHaveAttribute("aria-pressed", "true", { timeout: 2_000 });
  }).toPass({ timeout: 30_000 });

  // The URL is the state: the change is written there, not kept in a second copy.
  await expect(page).toHaveURL(/depth=3/, { timeout: 15_000 });

  // A reload restores the depth from the URL alone.
  await page.reload();
  await expect(page.locator(".segmented").getByRole("button", { name: "3" })).toHaveAttribute(
    "aria-pressed",
    "true",
    { timeout: 15_000 },
  );
});
