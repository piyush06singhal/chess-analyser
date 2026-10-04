import { expect, test } from "@playwright/test";

import { expectNoSeriousViolations } from "./a11y";

test("the map deep-link renders a neighbourhood from the URL", async ({ page }) => {
  await page.goto("/intelligence?tab=map&depth=2");

  // The URL is the state: the Map tab and the depth control reflect it on load.
  await expect(page.getByRole("button", { name: "Map", exact: true })).toHaveAttribute(
    "aria-current",
    "true",
  );
  await expect(page.locator(".segmented").getByRole("button", { name: "2" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );

  // Once a node is chosen the map draws an accessible graphic.
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
