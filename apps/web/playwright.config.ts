import { defineConfig, devices } from "@playwright/test";

// Browser + accessibility tests (§35/§36). They run against a live web app:
// either the one already serving (docker, or `npm run dev`) or, when nothing is
// on the port, a production server started here. The API is expected to be
// reachable too for the data-dependent specs, which skip cleanly when it is not.
const WEB_URL = process.env.ARGUS_WEB_URL ?? "http://localhost:3100";

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: WEB_URL,
    trace: "on-first-retry",
    // The app honours `prefers-reduced-motion` (globals.css collapses entrance
    // animations to 0.01ms). Scanning with reduced motion on measures the settled
    // DOM instead of a mid-fade frame — a partially-opaque element during a
    // 0.42s entrance animation is not a WCAG contrast defect, and scanning it as
    // one is a false positive. It also makes the layout assertions deterministic.
    reducedMotion: "reduce",
    // Every page is keyboard-reachable; the specs assert that, so no mouse-only
    // shortcuts are allowed to paper over a missing control.
  },
  // All three engines run the same specs: a product that claims keyboard and
  // responsive behaviour must be shown to hold in more than one browser engine.
  // WebKit and Firefox are exercised here, not assumed.
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
    { name: "firefox", use: { ...devices["Desktop Firefox"] } },
    { name: "webkit", use: { ...devices["Desktop Safari"] } },
  ],
  webServer: process.env.ARGUS_WEB_URL
    ? undefined
    : {
        command: "npx next start -p 3100",
        url: "http://localhost:3100",
        reuseExistingServer: !process.env.CI,
        timeout: 120_000,
      },
});
