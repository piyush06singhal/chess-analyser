import AxeBuilder from "@axe-core/playwright";
import { expect, type Page } from "@playwright/test";

// Only serious and critical findings fail the suite. A page can be perfectly
// usable with a minor contrast nit; failing on those teaches teams to disable
// the check, which is how accessibility gates die. Serious/critical is where a
// control becomes unusable with a keyboard or screen reader.
const FAILING_IMPACTS = new Set(["serious", "critical"]);

export async function seriousViolations(page: Page) {
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze();
  return results.violations.filter((violation) => FAILING_IMPACTS.has(violation.impact ?? ""));
}

/** Assert a page has no serious or critical WCAG A/AA violations. */
export async function expectNoSeriousViolations(page: Page, context: string): Promise<void> {
  const violations = await seriousViolations(page);
  const summary = violations
    .map(
      (violation) =>
        `  • ${violation.id} [${violation.impact}] ${violation.help}\n      ${violation.nodes
          .slice(0, 3)
          .map((node) => node.target.join(" "))
          .join("\n      ")}`,
    )
    .join("\n");
  expect(
    violations.length,
    `${context}: ${violations.length} serious/critical accessibility violation(s)\n${summary}`,
  ).toBe(0);
}
