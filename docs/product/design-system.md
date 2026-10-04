# Caissa — Design System

**Status:** current implementation. Tokens live in `apps/web/src/app/globals.css`;
shared primitives live in `apps/web/src/components/ui.tsx`.

## Approach

Caissa has one visual system, expressed as CSS custom properties plus a small set
of composed primitives. Pages compose primitives; they do not re-invent panels,
tabs and stat blocks. This is what keeps twenty pages looking like one product.

There is no component library dependency. The primitives are ~330 lines of typed
React and a single stylesheet, which keeps the bundle honest and the review small.

## Colour and theme

- One dark theme and one light theme, chosen by `data-theme` on `<html>`, applied
  before first paint by an inline script so there is never a flash of the wrong
  theme (`layout.tsx`, `THEME_INIT`).
- A palette of named scales — `ink` (surfaces), `mist` (text), and semantic
  accents `emerald`, `amber`, `rose`, `sky`, `violet`. Components reference the
  scales, never raw hex.
- **Colour is never the only signal.** Move quality, evaluation, training outcome
  and evidence source each carry a glyph, a label or a text badge in addition to a
  colour, so the meaning survives a monochrome display or colour-blindness.

## Typography

| Role | Face | Token |
| --- | --- | --- |
| UI / body | Geist Sans | `--font-geist-sans` |
| Code, move numbers, evals | Geist Mono | `--font-geist-mono` |
| Hero headlines only | Fraunces (display serif) | `--font-fraunces` |

The display serif is used **only** for hero statements (`.display-hero`). Body,
labels and data are sans or mono. A chess product's numbers are read as data, so
evaluations, move counts and CPL render in mono.

Type scale (from `globals.css`): `display-hero`, `text-2xl/…`, `text-body`,
`text-small`, `text-meta`. The `.label`, `.eyebrow`, `.subtitle`, `.mono` utility
classes pin the recurring roles.

## Spacing, sizing, radius

- Spacing follows a 4px base via Tailwind utilities (`gap-1.5`, `p-4`, `space-y-5`).
- Radii are `rounded-lg` (chips, small controls), `rounded-xl` (cards, inputs,
  nav items), `rounded-2xl` (panels). One radius per role, applied consistently.
- Panels share one width system: a page is a `max-w-[1520px]` column; content
  splits into `lg:grid-cols-[minmax(0,1fr)_320px]` or `lg:grid-cols-2`.

## Primitives (`ui.tsx`)

| Primitive | Job |
| --- | --- |
| `Panel` | Titled section with an optional subtitle (long subtitles fold into a disclosure) and actions |
| `Stat` | A single measured number with label, hint and tone |
| `Disclosure` | Collapsed "how / why" note — methodology stays one click away |
| `Note` | One line of prose inside a disclosure |
| `SourceBadge` | Where a claim came from (engine fact / Caissa interpretation / derived feature) |
| `Tabs` / `TabPanel` | Section switching with a valid, honest tablist |
| `Chip` | A small toggle (platform, filter) |
| `ForecastBar` | Three-way outcome bar, values supplied by the backend |
| `Field` | One key/value row |
| `ProgressBar` | A bounded progress indicator |

State components live in `empty-state.tsx`: `EmptyState`, `ErrorState`,
`SkeletonRows`, `InlineEmpty`.

## CSS component classes (`globals.css`)

Buttons (`.btn`, `.btn-primary`, `.btn-ghost`, `.btn-pill`), panels (`.panel`,
`.panel-header`, `.panel-body`), cards (`.card`, `.card-hover`), inputs
(`.input`), selects (`.select`), labelled fields (`.field`), inline links
(`.link`), tabs (`.tab`, `.tab-active`), chips (`.chip`, `.chip-active`),
badges (`.badge`, `.badge-source`), nav menus (`.nav-menu`, `.nav-menu-panel`),
stat (`.stat`, `.stat-value`, `.stat-hint`), disclosure (`.disclosure`),
progress (`.progress`, `.progress-bar`), and the animated backdrop
(`.page-backdrop`).

## Interaction states

Every interactive primitive defines: default, hover, focus-visible (a real ring,
not a colour shift), active/selected, and disabled. Focus is visible and keyboard
reachable on every control; `prefers-reduced-motion` disables the entry
animations.

## Charts and the board

- Charts (eval graph, intelligence map, progress) are drawn to a documented
  convention: measured values only, axis or caption stating the sample, and a
  keyboard-navigable list beside any visual. Colour follows the same semantic
  scale as the rest of the product.
- The chessboard (`chessboard.tsx`, `live-board.tsx`, `training-board.tsx`)
  shares one orientation/state model and one set of quality overlays, so the board
  looks and behaves the same wherever it appears.

## The honesty rule, applied to design

The design system encodes the product rule: a capability that is not built is
shown as unavailable *with its reason*, never hidden and never faked. There is no
component for "coming soon" that renders a live-looking control.
