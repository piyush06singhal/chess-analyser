// Lab measurement of per-route Web Vitals and gzip transfer size.
//
// This is *lab* data, not field data: it measures one cold load of each route on
// this machine against a locally running build. It exists because a field figure
// needs real traffic, which a self-hosted product with no users does not have —
// so rather than leave the metric UNKNOWN forever, this records the honest lab
// substitute and labels it as such.
//
// Run against the live web app:
//   ARGUS_WEB_URL=http://localhost:3100 node scripts/measure-lab.mjs
//
// It reports, per route: TTFB, FCP, LCP, CLS, the load event, and the gzip
// document transfer size (`transferSize` from the navigation entry, i.e. what
// actually crossed the wire, not the uncompressed file size).

import { chromium } from "@playwright/test";

const BASE = process.env.ARGUS_WEB_URL ?? "http://localhost:3100";
const ROUTES = ["/", "/dashboard", "/games", "/coach", "/training", "/intelligence", "/players", "/system"];

const browser = await chromium.launch();
const results = [];

for (const route of ROUTES) {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });

  // Register observers before any navigation so LCP/CLS are captured from the
  // very first paint.
  await page.addInitScript(() => {
    window.__lcp = 0;
    window.__cls = 0;
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) window.__lcp = entry.startTime;
    }).observe({ type: "largest-contentful-paint", buffered: true });
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) {
        if (!entry.hadRecentInput) window.__cls += entry.value;
      }
    }).observe({ type: "layout-shift", buffered: true });
  });

  await page.goto(BASE + route, { waitUntil: "load" });
  // Let client-side fetches and the entrance animation settle before measuring.
  await page.waitForTimeout(1500);

  const metrics = await page.evaluate(() => {
    const nav = performance.getEntriesByType("navigation")[0];
    const paint = performance.getEntriesByType("paint");
    const fcp = paint.find((entry) => entry.name === "first-contentful-paint");
    return {
      ttfb: nav ? nav.responseStart - nav.requestStart : null,
      fcp: fcp ? fcp.startTime : null,
      lcp: window.__lcp || null,
      cls: window.__cls,
      load: nav ? nav.loadEventEnd : null,
      transferKB: nav ? nav.transferSize / 1024 : null,
    };
  });

  results.push({ route, ...metrics });
  await page.close();
}

await browser.close();

const fmt = (value, unit = "ms", digits = 0) =>
  value === null || value === undefined ? "—" : `${value.toFixed(digits)}${unit}`;

console.log(`Lab Web Vitals — ${BASE} (cold load, 1280×900, Chromium)`);
console.log("This is lab data, not field data (no real traffic).");
console.log("-".repeat(88));
console.log(
  ["route".padEnd(14), "TTFB".padStart(8), "FCP".padStart(8), "LCP".padStart(8), "CLS".padStart(7), "load".padStart(8), "doc gz".padStart(9)].join(" "),
);
for (const r of results) {
  console.log(
    [
      r.route.padEnd(14),
      fmt(r.ttfb).padStart(8),
      fmt(r.fcp).padStart(8),
      fmt(r.lcp).padStart(8),
      fmt(r.cls, "", 4).padStart(7),
      fmt(r.load).padStart(8),
      fmt(r.transferKB, "KB", 1).padStart(9),
    ].join(" "),
  );
}
