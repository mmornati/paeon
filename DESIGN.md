---
name: Paeon
description: An incident management console. Incidents grouped by the stage they wait for, status as badges, the model's words in violet.
colors:
  bg: "#f5f6f8"
  surface: "#ffffff"
  surface-2: "#f0f2f5"
  border: "#e2e5ea"
  border-strong: "#c9ced7"
  text: "#141821"
  text-2: "#4f596a"
  text-3: "#667082"
  accent: "#3451d1"
  accent-bg: "#ecf0fe"
  accent-ink: "#ffffff"
  model: "#6a3fc0"
  model-bg: "#f2edfc"
  crit: "#c7253a"
  crit-bg: "#fdecee"
  warn: "#a35400"
  warn-bg: "#fdf2df"
  info: "#1565bd"
  info-bg: "#e7f1fd"
  ok: "#17754b"
  ok-bg: "#e5f6ed"
  bg-dark: "#0b0d12"
  surface-dark: "#12151c"
  surface-2-dark: "#191d27"
  border-dark: "#232835"
  border-strong-dark: "#353c4d"
  text-dark: "#e7eaf0"
  text-2-dark: "#a3acbd"
  text-3-dark: "#7f899c"
  accent-dark: "#8da2ff"
  accent-bg-dark: "#1a2142"
  accent-ink-dark: "#0b0d12"
  model-dark: "#b9a0ff"
  model-bg-dark: "#211a3a"
  crit-dark: "#ff7480"
  crit-bg-dark: "#3a171d"
  warn-dark: "#f3b045"
  warn-bg-dark: "#33270e"
  info-dark: "#62adff"
  info-bg-dark: "#11263e"
  ok-dark: "#4bcb8e"
  ok-bg-dark: "#102a20"
typography:
  title:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif"
    fontSize: "1.5rem"
    fontWeight: 700
    lineHeight: 1.2
    letterSpacing: "-0.02em"
  lead:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif"
    fontSize: "1.0625rem"
    fontWeight: 500
    lineHeight: 1.45
  body:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif"
    fontSize: "0.9375rem"
    fontWeight: 400
    lineHeight: 1.5
    fontFeature: "tabular-nums"
  small:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif"
    fontSize: "0.8125rem"
    fontWeight: 400
  label:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif"
    fontSize: "0.75rem"
    fontWeight: 600
  figure:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif"
    fontSize: "1.5rem"
    fontWeight: 700
    letterSpacing: "-0.02em"
  mono:
    fontFamily: "ui-monospace, 'SF Mono', 'Cascadia Mono', 'Roboto Mono', Menlo, Consolas, monospace"
    fontSize: "0.8125rem"
    fontWeight: 400
rounded:
  card: "0.625rem"
  control: "0.375rem"
  pill: "999px"
spacing:
  gutter: "1rem"
  gutter-wide: "2rem"
  card-inset: "1.125rem"
  card-gap: "1rem"
  row-gap: "0.5rem"
components:
  card:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    rounded: "{rounded.card}"
    padding: "1rem 1.125rem"
  nav-item:
    textColor: "{colors.text-2}"
    typography: "{typography.small}"
    rounded: "{rounded.control}"
    height: "2.25rem"
  nav-item-current:
    backgroundColor: "{colors.accent-bg}"
    textColor: "{colors.accent}"
  badge:
    backgroundColor: "{colors.surface-2}"
    textColor: "{colors.text-2}"
    typography: "{typography.label}"
    rounded: "{rounded.pill}"
    height: "1.375rem"
    padding: "0 0.5rem"
  badge-model:
    backgroundColor: "{colors.model-bg}"
    textColor: "{colors.model}"
  button:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    rounded: "{rounded.control}"
    height: "2.5rem"
    padding: "0 0.875rem"
  button-pressed:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.accent-ink}"
  button-danger:
    backgroundColor: "{colors.crit}"
    textColor: "{colors.accent-ink}"
  field:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    rounded: "{rounded.control}"
    height: "2.5rem"
---

# Design System: Paeon

## Overview

The console is an incident management app, in the family of the tools an operator already
knows: a sidebar, a list of incidents grouped by where they stand, status as coloured badges,
a detail page with a summary header and a timeline. It is clean and quiet: neutral surfaces,
one accent, colour kept for state.

It replaces the earlier "Strip Bay" console. Three things carry over, because they come from
the product and not from the old look:

- **The model's words look different from what code recorded.** They are violet, and the
  section or line also says so in words (a `Model` badge, a `model` or `tool` tag). A fact about
  a model call (tokens, cost, failure) is plain text.
- **Severity and state are always also a word.** The colour of a badge is never the only sign.
- **Nothing is loaded from another host**, and there is no inline style: the content policy
  refuses both. System fonts, one stylesheet, two small scripts, inline SVG icons.

## Colors

Every colour is a token in `console.css`, written once as `light-dark(light, dark)`; the
frontmatter lists both values (`-dark` suffix). The scheme follows the system, unless the
operator used the switch in the sidebar (`data-theme` on `<html>`, kept in `localStorage`).

- **Neutrals**: `bg` (page), `surface` (cards, sidebar, inputs), `surface-2` (table heads, code,
  notes, neutral badges), `border` and `border-strong`, and three text levels (`text`, `text-2`
  for secondary lines, `text-3` for labels and times).
- **Accent** (`accent`, `accent-bg`): links, focus ring, the current page in the navigation, a
  pressed button, citation chips, meters and chart bars, an incident that just changed.
- **Model** (`model`, `model-bg`): what a model said, and the dot of the Investigation stage.
- **State**: `crit` (critical, firing, failed, the kill switch), `warn` (warning, draft,
  cancelled, a claim with no tool call behind it), `info` (info severity, waiting, running),
  `ok` (resolved, verified, a stage that is on). Each has a tinted `-bg` for its badge.

State colours appear on badges, on the 3px severity edge of an incident, and on a state value
inside an incident's boxes. They are not used for decoration.

## Typography

System sans for everything read, system monospace for what is counted or cited: ids, times,
costs in tables, event kinds, catalog actions, payloads. Tabular numerals are on for the page.

- **Title** 1.5rem/700: the page heading. **Lead** 1.0625rem/500: the first statement of a card.
- **Body** 0.9375rem. **Small** 0.8125rem: secondary lines, tables, notes.
- **Label** 0.75rem/600: badges, box labels, table heads. Sentence case; uppercase is kept for
  the group headings of the board and the sub-headings inside a card.
- **Figure** 1.5rem/700: the number of a stat or of a pipeline tile.

## Layout

Phone first. One column with a 1rem gutter; from 60rem a 15rem sidebar stands at the left and
the gutter widens to 2rem. Reading pages are capped at 60rem, the board at 76rem.

- **Shell**: on a phone, the brand and theme switch, then the navigation as a row that scrolls
  sideways, then the stages on one line. From 60rem, a sticky sidebar: brand, navigation with
  icons, and the stages at the bottom with their state. The stages always say what is on.
- **Board**: a filter box, the pipeline (one tile per stage with its count, a link to that
  group), then the groups. Each incident is one card: severity badge, id, title, flags, when,
  and one labelled box per stage. Boxes are one line each; reasons are on the incident page.
- **Incident**: breadcrumb, a summary header (badges, title, the same boxes with their reasons),
  sticky section tabs, then one card per section: Alerts, Triage, Investigation, Plan, Record.
- **Ledger and Actions**: a row of stat tiles, then cards. Tables sit flush inside their card
  and drop their token columns under 44rem, printing them as a second line.

## Elevation and shape

Flat. Cards are `surface` on `bg` with a 1px border and, in light only, a 1px shadow. Corners:
0.625rem for cards, 0.375rem for controls, full pills for badges and filter chips.

## Components

- **Badge**: a pill, 1.375rem tall, label type, tinted by state, with a dot when it is a state.
  Variants: severity, incident status, alert state, plan outcome, risk, `Model`, tag (outlined:
  flapping, maintenance), `updated` (solid accent, set by the live refresh).
- **Pipeline tile**: stage name with its dot, and the count as a figure. Empty is dimmed. "Your
  answer" is tinted with the accent when a plan is waiting.
- **Incident card**: 3px severity edge at the left, dimmed when resolved. The whole card is the
  link. A state value in a box is coloured and dotted by tone: active, bad, good, warn, mute.
- **Card** (`.block`): a head (title, and a badge or a fact at the right) over a body. Inside:
  lead, facts, a note (tinted, with a left rule: what was not done, what a thing cannot do),
  bordered lists (alerts, evidence, plan steps with numbered circles).
- **Record row**: time in monospace, a voice tag (`in` blue, `code` neutral, `model` and `tool`
  violet, `sent` green, `you`), the line, then its kind, cost and duration in monospace. A
  disclosure shows the payload. The row a citation points to is tinted with an accent edge.
- **Cite chip**: `c1`, `c2` in monospace on the accent tint, a link to the tool call. When no
  tool call stands behind a claim it is an outlined warning chip that says so in words.
- **Document** (`.md`): a postmortem rendered from its markdown on one card, prose capped at
  46rem: ruled section headings, tinted quotes, bordered tables, code on `surface-2`, action
  items as ticked or empty boxes. The source sits under it in a disclosure. A `mermaid` block
  is drawn by the bundled library in a bordered figure, in its neutral or dark theme to match
  the scheme; that page alone allows inline styles, because the library needs them.
- **Buttons and fields**: 2.5rem tall. One neutral button; pressed is solid accent; the kill
  switch is solid red. There is no other primary action.
- **Stat tile, meter, chart**: a label, a figure, a line under it. Meters and the bars of the
  spend chart are SVG whose sizes are attributes, in the accent; a spent budget turns red and
  says "reached".

## Motion

On live refresh (every 10 seconds, paused while the tab is hidden or focus is on the board) an
incident that changed stage slides to its new place over 320ms, and is ringed and tagged
`updated` until pointed at or focused. Hover states ease over 150ms. Under reduced motion there
is no movement and no smooth scroll.

## Do and don't

- **Do** mark every word a model produced: violet, with a Model badge or a model tag nearby.
- **Do** write severity and state as a word inside the badge.
- **Do** say what was not done in a note: a stage that is off, a failed call, a spent budget.
- **Do** define a new colour as one `light-dark()` token and check both schemes.
- **Do** keep targets at 2.5rem for buttons, fields and navigation on a phone.
- **Don't** load a font, script, image or style from another host, or write a `style` attribute.
- **Don't** use a state colour for decoration, or violet for something code recorded.
- **Don't** let an incident card on the board grow: reasons belong to the incident page.
- **Don't** add a control that approves or runs anything: the console shows, and can only stop.
