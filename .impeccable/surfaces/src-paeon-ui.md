---
version: 1
slug: "src-paeon-ui"
primary_target: "src/paeon/ui"
related_targets: []
---

# Console (src/paeon/ui)

Scope: the whole operator console served under `/ui`: board, incident page, event log, ledger.
Mode: Operate. Phone first (opened from an ntfy notification), desk review second.

Job: see where each incident stands, why a model said what it said, which tools it read, what
it cost, and what left the server. Read-only, except triage feedback.

Constraints: server-rendered, no build step, no external request (fonts, scripts), strict CSP,
every payload redacted before display. Approvals, actions and autonomy are not built: not shown.

## Direction contract

THESIS: Strip Bay. An incident is a flight progress strip, and the stage it stands in is the
bay it sits in. Refused: the dark observability dashboard of stat tiles over a table.

OWN-WORLD: a grey-green rack as ground; off-white printed strips ruled into fixed cells with
small caps labels; the id sits in a holder filled with the severity colour (red, amber, green,
grey). Two voices everywhere: what code recorded is printed ink, what a model said is pen blue
italic. What left the server carries a reversed ink tag. System sans, monospace for figures.
Hairlines, no shadows, no rounded panels.

STORY: the operator sees which bay a strip is in, reads the model's words as annotation and
not as fact, opens the strip, and finds every claim tied to the tool call behind it.

FIRST VIEWPORT: on a phone, the bar (Paeon, what is enabled, "takes no action"), then the bays
stacked: Triage, Investigation, Open, Resolved, each a labelled rack with its strips. On a
desk, the four bays stand side by side as columns.

FORM: air traffic control strip board; position 1 of the ordered list; seed 9b757a1d (chosen by
the user over the assigned direction). Signature interaction: on live refresh a strip slides
from its bay to the next and keeps a "changed" tag until it is touched. Code-led: no comp, the
image key had no credit.

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance

## Build path record

The project's recorded default is comp-led (`.impeccable/config.json`, the user's answer at
init). This surface was built code-led all the same, because no image could be generated: the
only image tool available was the API fallback, and its first render was refused with
`credit_balance_exhausted`. The user was told in chat that the build was switching to code-first
and then chose Strip Bay on a text-only decision page; they did not say words accepting the
switch. There is no comp, no decision comp and no build state for this surface. When image
generation is available again, a comp round for the next surface (or a re-comp of this one) is
owed to the recorded default.

The seed key 9b757a1d was printed by `concept-seed --scope direction --mode operate` in the
build session (assigned index 6; the user took the pick card instead).

Decided after the finish review: the model's voice is blue and slanted everywhere, one rule;
a fact about a model call (tokens, cost, failure) is printed ink. A changed strip stands out of
line and carries an outlined tag; the reversed tag is kept for what left the server. Board
strips are a fixed form of six boxes, one line each; reasons are on the incident page.
