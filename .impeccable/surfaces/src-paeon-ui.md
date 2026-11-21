---
version: 1
slug: "src-paeon-ui"
primary_target: "src/paeon/ui"
related_targets: []
---

# Console (src/paeon/ui)

Scope: the whole operator console served under `/ui`: incidents, incident page, event log,
spend (ledger), actions, autonomy, sources, channels, postmortems, sign-in and setup.
Mode: Operate. Phone first (opened from an ntfy notification), desk review second.

Job: see what needs an answer now, find any incident among hundreds, read why a model said what
it said and which tool call backs each claim, see what it cost and what left the server.

Constraints: server-rendered Jinja, one CSS file, a little vanilla JS, no build step, no request
to another host, strict CSP (no inline style or script), every payload redacted before display.
Lists that grow (incidents, log, postmortems) are paged by the server with a cursor, and their
filters live in the URL so a view can be bookmarked and shared.

## Direction contract

THESIS: Incident explorer. The console is an observability tool in the family the operator
already uses (Datadog, OpenObserve): every list is a dense, filterable, paged table under a
query bar and a time range, and every incident page leads with its stage outcomes and its
conclusion. Refused: stacks of cards, one per incident; sections that show a zero; a page that
grows without end.

OWN-WORLD: the current light and dark tokens (cool neutral ground, white or near-black panels,
one blue accent, red, amber, blue and green for state). 6px radius, 1px hairlines, no shadow on
panels. Table rows 34px, sticky column headers, small caps column labels, monospace for ids,
times, durations and costs. A five-square stage track per incident (triage, investigation,
plan, action, postmortem): violet filled when a model answered, green when code did it, red
when it failed, blue outline when waiting, empty when skipped, dashed when off. The model's
words stay violet; facts about a model call are ink.

STORY: the operator lands on incidents, sees "Needs you" first when it is not zero, narrows
with status, range, facets or a search, opens a row, and reads the stage outcomes and the
conclusion before the evidence, each claim tied to its tool call.

FIRST VIEWPORT: desk: a 200px navigation rail grouped Respond / Review / Configure, a 40px
status bar (stage modes, live, last update), then the page title with status segments
(All, Needs you, Open, Resolved, each with a count) and a range switch (24 h, 7 d, 30 d, all),
the query bar, a 90px incidents-per-day histogram, then facets (severity, verdict,
investigation, host) beside the table. Phone: status bar, title, segments, query, rows as three
lines (severity, id, age / title / stage track, verdict, cost), a bottom tab bar.

FORM: the category standard, played straight; the user named Datadog and OpenObserve as the
bar and approved a mock of this explorer on 2026-11-21. No concept seed was rolled: the
direction is user-pinned. Code-led.

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance

## History

- 2026-11-18: built as Strip Bay (flight strips in bays), then redirected by the user to a
  generic incident management look (sidebar, stage counts, rounded cards).
- 2026-11-19: channel and source settings added for an admin (`ui.settings`), plus a sign-in
  page for `ui.auth: local`.
- 2026-11-21: critique scored 23/40. The board did not scale past a few dozen incidents
  (5,000px desktop, 9,800px phone), empty stage sections took the first viewport, filtering was
  client-side text only. The user chose this explorer direction for the whole console.
