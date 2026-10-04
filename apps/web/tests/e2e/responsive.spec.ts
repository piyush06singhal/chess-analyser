import { expect, test, type Page } from "@playwright/test";

// Responsive layout is easy to claim and easy to break, so it is measured here
// rather than asserted in prose. Every primary surface is checked at three real
// viewports for the failure that actually hurts on a phone: content that is
// wider than the screen, so a control sits off the right edge and cannot be
// reached. The check is the document's own scroll width against the viewport —
// a table may scroll *inside* its own box, but the page must not.

const VIEWPORTS = [
  { name: "mobile", width: 390, height: 844 },
  { name: "tablet", width: 768, height: 1024 },
  { name: "desktop", width: 1280, height: 900 },
];

const PAGES: Array<[path: string, name: string]> = [
  ["/", "Landing"],
  ["/dashboard", "Dashboard"],
  ["/games", "Library"],
  ["/import", "Import"],
  ["/lab", "Position Lab"],
  ["/coach", "AI Coach"],
  ["/training", "Training"],
  ["/scenarios", "What-If Lab"],
  ["/players", "Players"],
  ["/opponents", "Opponents"],
  ["/collections", "Collections"],
  ["/intelligence", "Intelligence Explorer"],
  ["/progress", "Progress"],
  ["/search", "Search"],
  ["/live", "Live"],
];

async function horizontalOverflow(page: Page): Promise<number> {
  return page.evaluate(() => {
    const root = document.documentElement;
    return root.scrollWidth - window.innerWidth;
  });
}

for (const viewport of VIEWPORTS) {
  test.describe(`${viewport.name} (${viewport.width}px)`, () => {
    test.use({ viewport: { width: viewport.width, height: viewport.height } });

    for (const [path, name] of PAGES) {
      test(`${name} does not overflow horizontally`, async ({ page }) => {
        await page.goto(path);
        await page.waitForLoadState("load");
        // Let data-driven layout settle so the assertion sees the loaded state,
        // not a narrower skeleton.
        await page.waitForTimeout(500);
        const overflow = await horizontalOverflow(page);
        expect(
          overflow,
          `${name} at ${viewport.width}px is ${overflow}px wider than the viewport`,
        ).toBeLessThanOrEqual(1);
      });
    }
  });
}

test("the primary controls stay on screen at mobile width", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page.waitForLoadState("load");

  const nav = page.getByRole("navigation", { name: "Main" });
  await expect(nav).toBeVisible();

  const toggle = page.getByRole("button", { name: /theme|light|dark/i }).first();
  await expect(toggle).toBeVisible();
  const box = await toggle.boundingBox();
  expect(box).not.toBeNull();
  if (box) {
    expect(box.x).toBeGreaterThanOrEqual(0);
    expect(box.x + box.width).toBeLessThanOrEqual(391);
  }
});

// WCAG 2.2 SC 2.5.8 (Target Size, Minimum): every pointer target is at least
// 24×24 CSS px, or it has enough spacing around it. Inline text links are the
// documented exception, so only real controls (buttons, chips, buttons-as-links)
// are asserted.
test("interactive controls meet the minimum touch target", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  for (const path of ["/", "/dashboard", "/games", "/training"]) {
    await page.goto(path);
    await page.waitForLoadState("load");
    await page.waitForTimeout(400);
    const undersized = await page.evaluate(() => {
      const selector = "button, a.btn, a.chip, a.btn-primary, a.btn-ghost";
      const bad: { label: string; w: number; h: number }[] = [];
      for (const el of Array.from(document.querySelectorAll<HTMLElement>(selector))) {
        const rect = el.getBoundingClientRect();
        if (rect.width === 0 && rect.height === 0) continue; // not rendered
        if (getComputedStyle(el).visibility === "hidden") continue;
        if (rect.width < 24 || rect.height < 24) {
          bad.push({
            label: (el.textContent ?? el.getAttribute("aria-label") ?? el.tagName).trim().slice(0, 40),
            w: Math.round(rect.width),
            h: Math.round(rect.height),
          });
        }
      }
      return bad;
    });
    expect(undersized, `${path} has undersized controls: ${JSON.stringify(undersized)}`).toEqual([]);
  }
});

test("a grouped nav menu opens inside the viewport", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page.waitForLoadState("load");

  // Open the first grouped menu (a native <details>) and confirm its panel is
  // fully on screen, so a deeper surface is reachable on a phone.
  const summary = page.locator("nav[aria-label='Main'] details[data-menu] > summary").first();
  await summary.click();
  const panel = page.locator("nav[aria-label='Main'] details[data-menu][open] .nav-menu-panel").first();
  await expect(panel).toBeVisible();
  const box = await panel.boundingBox();
  expect(box).not.toBeNull();
  if (box) {
    expect(box.x).toBeGreaterThanOrEqual(0);
    expect(box.x + box.width).toBeLessThanOrEqual(391);
  }
});
