---
name: Paeon
description: The Strip Bay console. An incident is a printed strip, and the stage it waits for is the bay it sits in.
colors:
  rack: "#c5cfcb"
  well: "#b4bfbb"
  strip: "#f4f6f2"
  edge: "#8a9793"
  rule: "#c3ccc8"
  ink: "#101820"
  ink-2: "#3a474e"
  pen: "#1b3fa8"
  pen-2: "#3d58a6"
  bar: "#101820"
  bar-ink: "#f4f6f2"
  bar-dim: "#a9b6b2"
  sev-critical: "#b3261e"
  sev-critical-ink: "#ffffff"
  sev-warning: "#d9a400"
  sev-warning-ink: "#101820"
  sev-info: "#1d6f5c"
  sev-info-ink: "#ffffff"
  sev-unknown: "#5d6b70"
  sev-unknown-ink: "#ffffff"
  rack-dark: "#0f1613"
  well-dark: "#080d0b"
  strip-dark: "#1c2622"
  edge-dark: "#38443e"
  rule-dark: "#2c3933"
  ink-dark: "#e8eeea"
  ink-2-dark: "#a3b3ac"
  pen-dark: "#9db8ff"
  pen-2-dark: "#8ea2d6"
  bar-dark: "#050907"
  bar-ink-dark: "#e8eeea"
  bar-dim-dark: "#8d9c99"
  sev-critical-dark: "#ef6a5e"
  sev-critical-ink-dark: "#0f1613"
  sev-warning-dark: "#e6b422"
  sev-warning-ink-dark: "#0f1613"
  sev-info-dark: "#49b79b"
  sev-info-ink-dark: "#0f1613"
  sev-unknown-dark: "#8d9ca0"
  sev-unknown-ink-dark: "#0f1613"
typography:
  title:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif"
    fontSize: "1.375rem"
    fontWeight: 700
    lineHeight: 1.2
  lead:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif"
    fontSize: "1.0625rem"
    fontWeight: 500
    lineHeight: 1.4
  body:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif"
    fontSize: "0.9375rem"
    fontWeight: 400
    lineHeight: 1.45
    fontFeature: "tabular-nums"
  strip-title:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif"
    fontSize: "0.9375rem"
    fontWeight: 650
    lineHeight: 1.25
  entry:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif"
    fontSize: "0.875rem"
    fontWeight: 400
    lineHeight: 1.3
  small:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif"
    fontSize: "0.8125rem"
    fontWeight: 400
    lineHeight: 1.45
  heading-label:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif"
    fontSize: "0.8125rem"
    fontWeight: 700
    letterSpacing: "0.09em"
  label:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif"
    fontSize: "0.6875rem"
    fontWeight: 600
    letterSpacing: "0.06em"
  holder-id:
    fontFamily: "ui-monospace, 'SF Mono', 'Cascadia Mono', 'Roboto Mono', Menlo, Consolas, monospace"
    fontSize: "1.125rem"
    fontWeight: 700
    lineHeight: 1
  figure:
    fontFamily: "ui-monospace, 'SF Mono', 'Cascadia Mono', 'Roboto Mono', Menlo, Consolas, monospace"
    fontSize: "0.8125rem"
    fontWeight: 600
  meta:
    fontFamily: "ui-monospace, 'SF Mono', 'Cascadia Mono', 'Roboto Mono', Menlo, Consolas, monospace"
    fontSize: "0.75rem"
    fontWeight: 400
rounded:
  none: "0"
spacing:
  strip-gap: "0.3125rem"
  inset: "0.625rem"
  gutter: "0.75rem"
  bay-gap: "1.125rem"
  gutter-wide: "1.5rem"
  block: "1.75rem"
components:
  bar:
    backgroundColor: "{colors.bar}"
    textColor: "{colors.bar-ink}"
    height: "2.75rem"
    padding: "0 0.75rem"
  bar-nav-item:
    textColor: "{colors.bar-dim}"
    typography: "{typography.small}"
    height: "2.75rem"
    padding: "0 0.75rem"
  bar-nav-item-current:
    backgroundColor: "{colors.rack}"
    textColor: "{colors.ink}"
  bay-head:
    textColor: "{colors.ink}"
    typography: "{typography.heading-label}"
    padding: "0 0 0.1875rem"
  bay-well:
    backgroundColor: "{colors.well}"
    padding: "0.3125rem"
  strip:
    backgroundColor: "{colors.strip}"
    textColor: "{colors.ink}"
    rounded: "{rounded.none}"
  strip-holder:
    backgroundColor: "{colors.sev-unknown}"
    textColor: "{colors.sev-unknown-ink}"
    typography: "{typography.holder-id}"
    width: "4rem"
    padding: "0.4375rem 0.125rem 0.375rem"
  strip-holder-critical:
    backgroundColor: "{colors.sev-critical}"
    textColor: "{colors.sev-critical-ink}"
  strip-holder-warning:
    backgroundColor: "{colors.sev-warning}"
    textColor: "{colors.sev-warning-ink}"
  strip-holder-info:
    backgroundColor: "{colors.sev-info}"
    textColor: "{colors.sev-info-ink}"
  strip-holder-resolved:
    backgroundColor: "transparent"
    textColor: "{colors.ink}"
  strip-cell:
    textColor: "{colors.ink}"
    typography: "{typography.entry}"
    padding: "0.25rem 0.625rem 0.375rem"
  strip-cell-label:
    textColor: "{colors.ink-2}"
    typography: "{typography.label}"
  record-row:
    backgroundColor: "{colors.strip}"
    textColor: "{colors.ink}"
    typography: "{typography.entry}"
    padding: "0.375rem 0.625rem 0.4375rem"
  record-tag:
    textColor: "{colors.ink-2}"
    typography: "{typography.label}"
  record-tag-model:
    textColor: "{colors.pen}"
  record-tag-sent:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.strip}"
    padding: "0 0.25rem"
  cite:
    textColor: "{colors.ink}"
    typography: "{typography.label}"
    height: "1.75rem"
    padding: "0 0.4375rem"
  cite-hover:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.strip}"
  flag:
    textColor: "{colors.ink}"
    typography: "{typography.label}"
    padding: "0 0.3125rem"
  changed-tag:
    textColor: "{colors.ink}"
    typography: "{typography.label}"
    padding: "0 0.3125rem"
  button:
    backgroundColor: "{colors.strip}"
    textColor: "{colors.ink}"
    typography: "{typography.small}"
    rounded: "{rounded.none}"
    height: "2.75rem"
    padding: "0 0.875rem"
  button-hover:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.strip}"
  button-active:
    backgroundColor: "{colors.ink-2}"
    textColor: "{colors.strip}"
  button-disabled:
    backgroundColor: "transparent"
    textColor: "{colors.ink-2}"
  filter-chip:
    backgroundColor: "{colors.strip}"
    textColor: "{colors.ink}"
    typography: "{typography.small}"
    height: "2.25rem"
    padding: "0 0.6875rem"
  filter-chip-current:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.strip}"
  field:
    backgroundColor: "{colors.rack}"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    rounded: "{rounded.none}"
    height: "2.75rem"
    padding: "0.5rem 0.5625rem"
  table:
    backgroundColor: "{colors.strip}"
    textColor: "{colors.ink}"
    typography: "{typography.figure}"
    padding: "0.3125rem 0.625rem"
  meter:
    backgroundColor: "{colors.rule}"
    textColor: "{colors.ink}"
    height: "0.375rem"
---

# Design System: Paeon

## Overview

**Creative North Star: "The Strip Bay"**

The console is an air traffic control strip board. The ground is a grey-green rack. An incident is an off-white printed strip in a holder, ruled into labelled boxes. A bay is a stage of the pipeline: Triage, Investigation, Open, Resolved. Where a strip sits says what it is waiting for, so the board answers "where does everything stand" before a word is read.

Everything on a strip is either printed or written in pen. Printed ink is what code recorded. Pen blue, slanted, is what a model said. The operator reads the model's words as annotation and never as fact, then opens the strip and finds each claim tied to the tool call behind it. The system is flat and dense: hairlines, square corners, no shadow, no gradient, no picture. It is the opposite of the dark observability dashboard of stat tiles over a table, which this world refuses.

The console is server-rendered and makes no request to another host. That constraint shapes the look as much as the metaphor does: system fonts only, one stylesheet, one small script, no inline style anywhere.

**Key Characteristics:**
- A rack, bays and strips: position carries state.
- Two voices, one rule everywhere: printed ink for code, blue italic for a model.
- Severity is the fill of the holder, and is always also written as a word.
- Hairlines and square corners; depth comes from three flat tones (rack, well, strip).
- Small uppercase labels over plain values, figures in monospace with tabular numerals.
- Light and dark follow the system setting; a print palette puts the strips on white paper.

## Colors

A cool grey-green ground, near-black ink, one blue for the pen, and four severity fills. Every colour is defined once per scheme in the frontmatter; dark values carry the `-dark` suffix and replace the light ones under `prefers-color-scheme: dark`.

### Primary
- **Pen Blue** (`pen`): the model's voice. Text a model wrote, the focus outline, the caret, the selection background. Always italic when it is text.
- **Faded Pen** (`pen-2`): the model's secondary words (why a hypothesis was ruled out, why a step is suggested). Still italic.

### Secondary
- **Critical Red** (`sev-critical`), **Warning Amber** (`sev-warning`), **Info Green** (`sev-info`), **Unknown Grey** (`sev-unknown`): holder fills. Each has its own ink (`sev-*-ink`) for the id and the severity word printed on it. They appear on holders and nowhere else.

### Neutral
- **Rack** (`rack`): the page ground, and the ground of an input or a payload set into a strip.
- **Well** (`well`): the recess of a bay, behind its strips, and the day divider of the record.
- **Strip** (`strip`): the paper. Every surface that carries a record: strips, lists, tables, the feedback form.
- **Edge** (`edge`): the hairline around a strip-coloured surface and the footer rule.
- **Rule** (`rule`): the hairline inside a surface, between cells and rows, and the meter track.
- **Printed Ink** (`ink`): what code recorded. Also the 2px rule under a bay or section heading, the meter fill, and the reversed tag.
- **Second Ink** (`ink-2`): labels, times, standing lines, facts about a call.
- **Bar** (`bar`, `bar-ink`, `bar-dim`): the top bar. It stays dark in both schemes; the current page is a rack-coloured tab cut into it.

### Named Rules
**The Two Voices Rule.** What code recorded is printed ink, upright. What a model said is pen blue and italic. One rule on every page: a verdict, a summary, a claim, a suggested step are pen; a fact about a model call (its tokens, cost, duration, failure) is printed ink. The slant means the voice never rests on colour alone.

**The Reversed Tag Rule.** A tag reversed out of solid ink marks what left the server (a notification that was sent), and nothing else. A tag for anything that stayed inside is plain or outlined.

**The Written Severity Rule.** Severity lives in the fill of the holder and is always also written there as a word (critical, warning, info, unknown). Severity colour is never used for text, borders of other parts, or decoration.

**The Turned Over Rule.** A resolved strip keeps its severity colour as a 3px frame inside the holder, with a clear ground and printed ink. The colour says what it was; the empty holder says it is over.

## Typography

**Display Font:** the system sans (-apple-system, BlinkMacSystemFont, Segoe UI, Roboto, Helvetica Neue, Arial)
**Body Font:** the same system sans
**Label/Mono Font:** the system monospace (ui-monospace, SF Mono, Cascadia Mono, Roboto Mono, Menlo, Consolas)

**Character:** a printed form. One sans in a narrow range of sizes does the reading; small uppercase labels name each box; monospace is kept for things that are counted or cited. Tabular numerals are on for the whole page.

### Hierarchy
- **Title** (700, 1.375rem, 1.2): the page heading, and the title of the strip on an incident page.
- **Lead** (500, 1.0625rem, 1.4): the first statement of a section (the verdict, the summary, the day's spend). Also the Paeon wordmark at weight 700.
- **Body** (400, 0.9375rem, 1.45): running text. Blocks of prose stop at 70ch.
- **Strip title** (650, 0.9375rem, 1.25): the title of a strip on the board.
- **Entry** (400, 0.875rem, 1.3): the value in a cell and the text of a record row. Weight 500 when it is in pen.
- **Small** (400 or 600, 0.8125rem): standing lines, facts, notes, navigation, buttons, tables.
- **Heading label** (700, 0.8125rem, 0.09em, uppercase): bay names and section headings, each over a 2px ink rule.
- **Label** (600 or 700, 0.6875rem, 0.04em to 0.06em, uppercase): cell labels, column heads, record tags, flags, the stage line of the bar.
- **Holder id** (mono 700, 1.125rem, 1): the incident number in the holder. The severity word under it is sans 700 at 0.625rem, uppercase.
- **Figure** (mono 600, 0.8125rem) and **Meta** (mono 400, 0.75rem): counts, costs, table cells, times, event kinds, citation ids, payloads.

### Named Rules
**The Same Host Rule.** System fonts only. The pages load nothing from another host, so no webfont is ever added, whether hosted elsewhere or bundled.

**The Counted In Mono Rule.** Monospace is for what is counted or cited: ids, counts, costs, times, event kinds, payloads. A sentence is never set in it.

## Layout

Phone first. A single column with a 0.75rem gutter, widening to 1.5rem from 44rem. Reading pages (incident, log, ledger, event) are capped at 54rem and centred; the board is capped at 96rem.

The board is a grid of bays with a 1.125rem gap: one column on a phone, two from 44rem, four side by side from 78rem, where each well is at least 60vh tall so an empty bay still reads as a rack. Inside a well, strips are 0.3125rem apart with 0.3125rem of well showing around them.

A strip is a two-column grid: a 4rem holder, then the body. On the board every strip is the same fixed form: a one-line title, a one-line standing (when, how long, flags), and six boxes in two columns, one line each, cut with an ellipsis and carrying the full value as a title. The time and the standing never give way; the flag is the part that shrinks. On its own page the strip has room: boxes flow at 7.5rem minimum and values wrap and include their reason.

Density is tight and steady: 0.625rem inset inside surfaces, 0.25rem to 0.4375rem above and below a line, 1.75rem between sections. Tables drop their token columns under 44rem and print them as a second line under each row.

The bar is one 2.75rem line (wordmark, three destinations) with the stage line under it on a phone and beside it from 44rem. The stage line always says what is on, and that actions are none. The footer repeats that Paeon takes no action, and names the time zone.

**The Fixed Form Rule.** On the board a strip is six boxes, one line each, always the same height. Reasons and long values belong to the incident page, not to the board.

## Elevation & Depth

There is no shadow anywhere. Depth is three flat tones: the rack, the well a shade deeper, and the strip lighter than both, each strip-coloured surface closed by a 1px edge hairline. Inside a surface, 1px rule hairlines divide cells and rows. A 2px ink rule sits under a bay name or a section heading and above a table total.

Two inset rings are drawn with an inset box-shadow because they must not change the size of the box: the 3px severity frame of a resolved holder, and the 2px ink ring on the record row a citation points to. They are frames, with no offset and no blur.

### Named Rules
**The Hairline Rule.** Surfaces are separated by tone and a 1px line. No drop shadow, no blur, no gradient, no translucent layer.

## Shapes

Every corner is square (radius 0), including buttons, inputs, chips and tags. Forms are rectangles ruled into smaller rectangles. Borders are 1px, except three deliberate weights: the 2px ink rule under headings, the 2px outline of the changed tag, and the 3px frame of a resolved holder. A dashed 1px border has one meaning: a claim with no tool call behind it. Meters are flat 0.375rem bars, ink on a rule-coloured track.

## Components

### Bar
The dark band across the top in both schemes. Wordmark at left, then Board, Log, Ledger as uppercase small labels (600, 0.06em) in the dim bar ink, each 2.75rem tall. The current page is a rack-coloured tab with printed ink, so the bar opens onto the page under it. Hover lifts the label to full bar ink. Focus is a bar-ink outline drawn 3px inside the item. The stage line under or beside it sets each stage name dim and its state bold; a stage that is off stays dim.

### Bay
A stage of the pipeline. The head is the name as a heading label at left and the count of strips in monospace at right, over a 2px ink rule. Under it the well holds the strips. An empty bay says so in one small line of second ink ("No incident is waiting for a verdict.").

### Strip
The signature component. A strip-coloured rectangle with an edge hairline.
- **Holder:** a 4rem column filled with the severity colour. The incident number at the top in monospace, the severity word at the bottom. Resolved: clear ground, 3px severity frame, printed ink.
- **Title:** strip title weight. On the board the whole strip is the link: hover turns the border to ink and underlines the title, keyboard focus puts the 2px pen outline around the whole strip.
- **Standing:** one small line in second ink: when it opened, and how long it has been open or took to resolve. Flags follow it.
- **Cells:** a ruled grid of labelled boxes. On the board, six fixed cells: Host, Alerts, Triage, Investigation, Postmortem, Cost. A value a model produced (the verdict, the confidence) is in pen; a state code set (off, skipped, running, failed) is printed.

### Flag
A small uppercase label in a 1px second-ink outline, after the standing line. It names a condition code observed about the incident (flapping, maintenance). On the board it is the part that truncates.

### Changed tag
An uppercase label in a 2px ink outline, placed after the standing line by the script. A strip that moved or changed on live refresh also stands 0.5rem out of line to the right. Both stay until the strip is pointed at, pressed or focused. The changed tag is outlined, never reversed.

### Record row
One event of the log, on a strip-coloured list ruled by hairlines. Three columns: the time in monospace second ink, a voice tag, then the entry with its event kind, cost and duration in monospace under it. Days are divided by a well-coloured band with an uppercase label.
- **Voice tags:** uppercase labels. `in` (an alert arrived), `code` and `you` (feedback) in second ink; `model` and `tool` in pen blue; `sent` reversed out of ink.
- **Text:** printed, unless the line is the model's own words, which are pen. What code observed about it follows as a small bold printed note.
- **Payload:** a disclosure that says "Show payload" or "Hide payload", opening a monospace block on the rack ground, scrollable to 26rem.
- **Target:** the row a citation points to carries a 2px ink ring.

### Cite chip
The link from a model's claim to the tool call behind it. A monospace id (c1, c2) in a 1px second-ink outline, at least 2.25rem by 1.75rem, upright even inside pen text. Hover reverses it to ink. When no tool call stands behind a claim, the chip is not a link: dashed outline, sans uppercase, and it says so in words.

### Button
Strip ground, 1px ink border, square, 2.75rem tall, small type at weight 600. Hover and pressed-state (the vote already given) reverse it to ink; active is second ink; disabled drops to an edge border on a clear ground. Colour changes ease over 160ms. There is one button style: the console has no primary action to promote.

### Filter chip
The log's filters: a horizontally scrolling line of strip-coloured links with an edge hairline, 2.25rem tall. Hover turns the border to ink; the current filter is reversed to ink.

### Field
A label in the label style above a square input, 2.75rem tall, on the rack ground with an edge hairline, so it reads as a box cut into the strip. Hover darkens the border to second ink; focus turns it pen with the 2px pen outline flush to it.

### Table
Strip ground, edge hairline, rule hairlines between rows. Column heads are labels; cells are monospace; numbers are right-aligned and never wrap. The total row sits above a 2px ink rule in bold.

### Meter
A share of a budget or of the dearest day: a flat bar 0.375rem tall, ink on the rule colour, drawn as an SVG whose width is an attribute. It is never coloured by how full it is; the figure and a printed note say when a budget is spent.

### Lists of an incident
Alerts, evidence, the suggested plan, budgets and tallies are all the same surface: strip ground, edge hairline, one line per item, rule hairlines between. The plan numbers its steps in monospace second ink. A risk is an uppercase label in second ink; high risk is ink with a 2px underline.

### Motion
One movement: on live refresh (every 10 seconds, paused while the tab is hidden or focus is on the board) a strip that changed bay slides to its new place over 420ms on `cubic-bezier(0.16, 1, 0.3, 1)`, with a 120ms cross-fade of the rest. Under reduced motion the swap is instant. The 160ms colour eases on hover are state feedback, not movement.

## Do's and Don'ts

### Do:
- **Do** set every word a model produced in pen blue italic, and every fact code recorded in upright printed ink, on every page.
- **Do** write severity as a word in the holder whenever the holder is filled or framed with its colour.
- **Do** keep the reversed ink tag for what left the server.
- **Do** build new surfaces from the strip ground with a 1px edge hairline and 1px rule hairlines inside, corners square.
- **Do** label every box with a small uppercase label and put the value under it in plain type.
- **Do** set ids, counts, costs, times and event kinds in monospace.
- **Do** say what was not done in a printed line: a stage that is off, a call that failed, a budget that is spent, a claim with no tool call behind it.
- **Do** keep interactive targets at 2.75rem tall for buttons, fields and navigation.
- **Do** set per-element values without inline style: SVG attributes for meters, the CSSOM from script for view-transition names.
- **Do** define every new colour for light and dark together, and check the print palette.

### Don't:
- **Don't** use pen blue for anything code recorded, or use italic for emphasis: the slant belongs to the model.
- **Don't** use a severity colour outside a holder, or let colour be the only sign of severity.
- **Don't** reverse a tag for anything that stayed on the server; the changed tag is outlined.
- **Don't** add a shadow, a gradient, a blur or a rounded corner.
- **Don't** load a font, script, image or style from another host, and don't add a webfont.
- **Don't** write a `style` attribute or a `<style>` block: the content policy refuses them.
- **Don't** let a strip on the board grow: no wrapped title, no seventh box, no reason text.
- **Don't** add a second motion. A strip sliding between bays is the only movement, and it is off under reduced motion.
- **Don't** build the dashboard of stat tiles over a table.
- **Don't** show approvals, actions or autonomy controls as if they existed.
