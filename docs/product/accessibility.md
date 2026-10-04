# Caissa — Accessibility

**Target:** WCAG 2.1 level AA on the primary surfaces.
**Status:** the primary surfaces are scanned with axe on every run of the browser
suite and pass with no serious or critical violations. The limitations at the end
are real and stated.

## What is verified, and how

The browser suite `apps/web/tests/e2e/accessibility.spec.ts` runs **axe-core**
(`@axe-core/playwright`) over every primary page and asserts **no serious or
critical violations**. The page list is the navigation's own jobs, so a new
top-level page is one line in that file.

```bash
cd apps/web
npx playwright test tests/e2e/accessibility.spec.ts
```

The suite covers: Dashboard, Library, Import, Position Lab, AI Coach, Coach ·
Today, Training, What-If Lab, Players, Opponents, Collections, Intelligence
Explorer, Progress, Search, Live and System Health — plus a keyboard check that
the first Tab stop lands on a real, focused control.

## What is implemented in the code

- **Semantic HTML.** `header`/`nav[aria-label="Main"]`, `main`, `section`,
  `footer`, `table` with `columnheader` cells, `ol`/`li` for ordered steps, and
  `dl`/`dt`/`dd` for key/value rows.
- **Landmarks and headings.** One `h1` per page, a labelled main navigation, and
  a page structure a screen reader can skim.
- **Keyboard operation.** Every control is a real `button`/`link`/`input`. The nav
  dropdowns are native `<details>` elements, so they open and close from the
  keyboard with no custom key handling. Tabs use `role="tablist"`/`role="tab"`
  with `aria-selected`; panels are conditionally rendered, so a panel id is only
  referenced when the element exists (an `aria-controls` pointing at an absent
  panel is a critical axe violation — it is deliberately omitted, with the reason
  recorded in `ui.tsx`).
- **Visible focus.** Focus-visible rings are defined on every interactive class in
  `globals.css`, not left to the browser default.
- **Labels and names.** Inputs carry labels (visible or `sr-only`); icon-only
  controls carry `aria-label` or `title`; decorative glyphs are `aria-hidden`.
- **Non-colour signals.** Move quality, evaluation, training outcome and evidence
  source each carry a glyph, a label or a text badge in addition to colour.
- **Charts and the map.** Every visual has a text equivalent: the intelligence map
  repeats its edges as a keyboard-navigable list, the forecast bar exposes its
  values through an `aria-label`, and stat blocks are text.
- **Reduced motion.** `prefers-reduced-motion` disables the entry animations and
  the animated backdrop.
- **Chess board.** The interactive boards (training, live) carry a keyboard layer
  (`board-keyboard.tsx`): a transparent grid over the board whose 64 squares are
  real, named buttons ("e2, white pawn"), one roving tab stop, arrow keys to move
  the cursor and Enter/Space to pick a piece up and set it down. Playing a move
  no longer requires a pointer. The visual board's own library pieces are taken
  out of the tab order and the accessibility tree, so a screen reader hears the
  named grid instead of thirty-two unlabelled controls. The read-only analysis
  board stays a single image with the FEN as its text alternative, and the move
  list beside every board is keyboard navigable.

## Honest limitations

- **Automated scans are not a manual audit.** axe catches machine-detectable
  issues (names, roles, contrast on the scanned DOM, landmark structure). It does
  not judge whether the reading order makes sense to a person, and it does not
  evaluate a live region's timing. No claim is made that a manual screen-reader
  walkthrough has been completed for every flow.
- **The chessboard is a custom widget.** Keyboard *movement* works on the
  interactive boards via the overlay grid; the focus cursor and the board's own
  drag highlight are independent, and the overlay aligns to the board's box rather
  than to each square's pixel bounds, so on an unusually sized board the focus
  ring can sit a fraction inside a square.
- **The map's force-directed layout is visual.** It is duplicated as a list, but
  the spatial layout itself carries no accessible equivalent beyond that list.
- **Contrast is checked on the scanned pages.** A page added later without being
  added to the axe page list would not be scanned; the list is the contract.

These are boundaries, not oversights hidden behind a green check.
