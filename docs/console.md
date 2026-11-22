# The console

The console is the event log rendered for a person, under `/ui`. It is where you see why Paeon
said what it said: what was taken into account, which model or tool was called, what it cost,
and what was sent or done. It reads like the observability tools you already use: every list is
a filterable, paged table under a search box and a time range, and every incident leads with
what each stage concluded.

![The incident explorer: status tabs, a time range and a search box, incidents opened per day, filters beside the table](assets/console/explorer.webp)

Every screenshot on this page comes from synthetic data (`tests/ui_data.py`), taken with
`scripts/console_shots.py`: no real server, alert or model answer is shown.

## Turn it on

```yaml
ui:
  enabled: true
  require_header: X-authentik-username   # optional
  proxy_secret: {}                       # optional, recommended: see below
```

Then open `http://paeon:8080/ui`.

!!! warning "By default the console has no login"

    It is off until you enable it, and unless you set `auth: local` (below) it must sit behind
    a reverse proxy that authenticates (Traefik with Authentik forward-auth, for example): route
    `/ui` through it and do not publish Paeon's port. `require_header` names a header your proxy sets once the user is authenticated;
    a request without it is refused. That is a second net against a route that bypasses the
    proxy, not a login: anyone who can reach the port directly can send the header. The ingest
    endpoints keep their bearer token either way.

### A secret only the proxy knows

The proxy's network is usually shared: every container on it can reach Paeon's port and send
any header. `proxy_secret` closes that. Put a long random value in `PAEON_UI_PROXY_SECRET`
(24 characters or more) and have the proxy add it to each request as `X-Paeon-Proxy`; a request
without that exact value is refused. Both names can be changed (`header`, `value_env`).

With Traefik, a second middleware after the one that authenticates:

```yaml
http:
  middlewares:
    paeon-proxy-secret:
      headers:
        customRequestHeaders:
          X-Paeon-Proxy: "the same value as PAEON_UI_PROXY_SECRET"
  routers:
    paeon:
      middlewares: [authentik@file, paeon-proxy-secret]
```

### Without a proxy: one password

For a server with no authenticating proxy, the console can ask for a password itself:

```yaml
ui:
  enabled: true
  auth: local
```

![The first-run page: the code from the log, then the password, twice](assets/console/setup.webp)

Set `PAEON_SECRET_KEY` (32 characters or more, any long random value). The password is chosen
in the browser the first time: Paeon prints a code in its log at each start until a password
exists, and `/ui/setup` asks for that code, so that only someone who can read the log of this
server can choose it. Only a salted hash of the password is kept. To set it from the
environment instead, or to get back in after forgetting it, set `PAEON_UI_ADMIN_PASSWORD`
(12 characters or more): it takes over. Whoever signs in is the admin. The session is a signed cookie that lasts 14
days; changing the password or the key signs everyone out. After five wrong passwords in a row
from one address, no password from that address is looked at for a minute; after fifty
in a row from anywhere, none is for anyone. There is one account and no user list: for several
people, use a proxy and roles. Serve it over TLS as soon as it leaves your own network.

### Roles

Behind a proxy, `roles` says who may do what, from the groups the proxy names for each request:

```yaml
ui:
  enabled: true
  proxy_secret: {}
  roles:
    header: X-authentik-groups     # the default; Authelia: Remote-Groups, separator ","
    admin: [paeon-admins]
    operator: [paeon-operators]
```

| Role | Can |
|---|---|
| viewer | Read every page. Anyone the proxy lets in who is in no listed group. |
| operator | Also say whether triage was right, re-check or close an incident, review and edit postmortems, stop automation, and answer a waiting plan where the executor allows it. |
| admin | Also add, change, test and remove channels, when `settings` is on. |

`roles` needs `proxy_secret`: a groups header anyone on the network can send would prove
nothing. Without `roles`, everyone the proxy lets in is an operator, as in earlier versions,
and nobody is an admin.

### With no rules file at all

```bash
PAEON_CONSOLE=local
```

With no `PAEON_CONFIG`, this variable alone turns the console on with `auth: local` and
`settings: true`: start Paeon, open `/ui`, choose the password, and add your sources and
channels there. The incidents page lists what is left to do, above the explorer, until it is done.

![Finish setting up: connect a source, send a first alert, add a channel](assets/console/getting-started.webp)

Stages that call
a model, and everything about acting, are still configured in a rules file; when you write
one, remove `PAEON_CONSOLE` and set `ui` in the file. What you added in the console stays, as
long as the file keeps `settings: true`.

## Channels and sources in the console

```yaml
ui:
  enabled: true
  auth: local        # or roles with an admin group
  settings: true
```

With `settings` on, an admin adds notification channels on the Channels page: choose the
service, name the channel, fill in its settings, choose what it carries, and send a test.

![The Channels page: a channel added here, just tested](assets/console/channels.webp)

![A channel added in the console: its token is kept and never shown again](assets/console/channel-edit.webp)
A channel added there takes effect at once, without a restart. This also needs
`PAEON_SECRET_KEY`.

- **The rules file stays the source for what it defines.** The console never writes it. Its
  channels are shown marked `rules file` and can be tested from the console, not changed. A
  name the file defines cannot be taken in the console; if the file later claims a name the
  console used, the console's channel is set aside and the page says so.
- **Console channels live in the database**, next to the event log. A secret typed in the
  browser is encrypted with `PAEON_SECRET_KEY` before it is stored, is never shown again, and
  is deleted with its channel. Changing where a channel sends (its server, host or port)
  asks for its secrets again: a stored secret is never sent to a new address. Lose the key and those channels are set aside until you save
  them again with their secrets. Back up the database and keep the key somewhere else.
- **They carry the digest and the analysis, never an approval.** Approvals are sent by the
  executor, from its own copy of the rules file, next to the key that signs them.
- **The log says who changed what**: the service, what the channel carries and who saved it.
  It holds neither an address nor a secret.
### Sources

![The Sources page: a source of the rules file, Paeon's own webhooks, and a Prometheus added here](assets/console/sources.webp)

The Sources page works the same way, for the monitoring services of
[Sources](sources.md): choose the service, name the source, give its server and what an
investigation may read in it, then save and test. A source added there is connected at once:

- it receives at `/ingest/<name>` if its service pushes alerts (the page shows the address);
- it is polled if its service is polled, and reported stale like any other;
- its tools are offered to the next investigation, and withdrawn when it is removed.

What you allow in a source is what a model provider may be shown, which is why adding one
takes the admin role. The names the rules file defines, and those Paeon answers on its own
(`alertmanager`, `uptime-kuma`), cannot be taken. Changing a source's server asks for its
secrets again.

- **With `settings` off, the file is all there is**: channels and sources added in the console are kept
  and ignored. That is the setting for a deployment configured from git.

## Finding your way

On a desk, a rail on the left groups the pages by what you came to do:

| Group | Pages |
|---|---|
| **Respond** | **Incidents**, with the number still open, and **Actions** (with actions on). |
| **Review** | **Log**, **Postmortems** (with the number of drafts nobody reviewed yet) and **Spend**. |
| **Configure** | **Sources**, **Channels** (once a channel exists, or with `settings`) and **Autonomy** (with actions on). |

The bar at the top says how each stage runs (triage deciding what is investigated, investigation read-only,
postmortems as drafts, actions by the executor), and holds the light or dark switch. On a
phone the rail becomes a tab bar at the bottom: the first four pages, then **More** for the
rest.

| Page | |
|---|---|
| **Incidents** | The incident explorer: every incident, filtered and paged. See below. |
| **Incident** | What each stage concluded, then the investigation with each claim tied to the tool call behind it, the plan and what became of it, triage, and the full record of events. |
| **Log** | Everything recorded, newest first: searched, narrowed to one kind or one incident. |
| **Spend** | The cost of model calls over 7, 14 or 30 days, by stage, against the daily budgets. |
| **Sources** | Every monitoring service: how its alerts arrive (with the address it posts to), the tools it offers an investigation, how many alerts it fired in the last 30 days, and whether a polled one has gone stale. Never its address or its secret. With `settings`, an admin adds, tests, edits and removes sources here. |
| **Channels** | Every notification channel a stage sends to, what it carries, and how its deliveries of the last 30 days went. With `settings`, an admin adds, tests, edits and removes channels here. |
| **Actions** | The kill switch, the plans waiting for your answer, every plan of the last 30 days with its outcome, the catalog and the policies. |
| **Autonomy** | Per alert, action and container: the level in force, the track record, what was demoted and why, and the promotions that were earned, as the line to add to your rules file. |
| **Postmortems** | The drafts and their review status, as a table with tabs for those to review and those reviewed. A draft is shown rendered, with its markdown source one click away. |

## The incident explorer

The incidents page lists every incident, open ones first, then the rest by the day of their
last activity. From top to bottom:

- **Search** matches the title or the host; `#42` (or `42`) finds incident 42. Press `/` to
  jump to the box, Escape to clear it.
- **Range**: 24 h, 7 d (the default), 30 d or all time. An incident still open is shown
  whatever the range.
- **Status tabs**: All, **Needs you** (a plan waiting for your Go), Open and Resolved, each
  with its count. Needs you stands out when it is not zero.
- **The chart** counts the incidents opened per day (per hour over 24 h, per week over a long
  history), stacked by severity, and follows the filters.
- **Filters** beside the table: severity, what triage did, what the investigation did, and the
  host, each value with its count. **Clear filters** drops them all.
- **The table**: severity, number, title with its tags (`flapping`, `maintenance`,
  `quiet 3 days`, `needs your answer`), host, the stage track, what triage said, when it opened,
  how long it lasted, and what its model calls cost.
- **Paging**: 50 incidents a page, with **Older** and **Newest**. A new incident never shifts
  the page you are on.

The **stage track** is five squares, one per stage: triage, investigation, plan, action,
postmortem. Violet means a model answered, green that code did it, red that it failed, a blue
outline that it is waiting, an empty square that it was skipped, a faint one that the stage is
off. The legend under the table says the same.

Every filter is in the URL, so a view can be bookmarked or sent to someone. The page refreshes
itself while it is open.

![The explorer over 30 days, filtered on critical incidents of one host](assets/console/explorer-filtered.webp)

![Needs you: the plans waiting for your answer](assets/console/explorer-needs-you.webp)

## An incident, top to bottom

The header says where the incident stands: severity, status, its tags, when it opened and for
how long. Under the title, the **stage strip** has one cell per stage, with what it came to:
the triage verdict and how sure, how sure the investigation was and how many tools it called,
the plan, what became of it, and the postmortem. Then the **conclusion**: the investigation's
summary, or triage's reason when nothing was investigated, marked as the model's.

![An incident page: the stage strip, the conclusion, then the investigation](assets/console/incident.webp)

Below, tabs jump to each section: **Investigation**, **Plan**, **Triage**, **Still open?** and
**Record**. On the side, the facts (host, times, cost), the alerts with their state, and your
review of triage.

The investigation shows the impact and the root cause with its confidence. Each claim under
**Evidence** links to the tool call behind it. A claim that cites no real call says so.

![The investigation: impact, root cause, evidence tied to tool calls, what was ruled out](assets/console/investigation.webp)

The **record** is folded: open it for every event of the incident, oldest first, each with its
payload. Its heading says how many events there are and what they cost.

![The record, unfolded](assets/console/record.webp)

With actions on, the plan says what was proposed, at which level, and what became of it. A plan
held under `approve` says what held it: a policy line (`a policy sets it to observe`), the default
level, the kill switch, the hourly limit. The analysis on your phone says the same, so a plan
that is only recorded never reads like one waiting for a Go. While it waits, the console tells
you where the Go is: on your phone, and, if the executor allows it, here.

### Go and No-go in the console

Off by default. In the **executor's** copy of the rules file:

```yaml
actions:
  console_approval: true
```

Put the same line in the core's copy, so that the console shows the buttons. An operator then
sees **Go: run it** and **No-go** on a plan that waits for an answer. It is the same answer as
the buttons on your phone: the first one counts, the phone is told it was given in the
console, and the record keeps who gave it. Only a plan the executor is already waiting on can
be answered, within the catalog and the policies: a plan recorded at `observe` or `suggest` has
no button, and changing that is a change of policy. The executor decides from its own copy of
the file, so the core's copy alone cannot turn it on. What it costs is in the
[threat model](threat-model.md#the-console-and-the-api).

![A plan waiting for a Go, with Go and No-go in the console](assets/console/plan-console-go.webp)

Once the executor ran a plan, the same section says so, and every step is in the record.

![A plan a runbook ran without asking: it ran and the result was verified](assets/console/plan-verified.webp)

### Still open?

An incident closes by itself when its alerts resolve. Some sources never send that: OpenObserve
writes a `normal` row only when it evaluates a healthy alert, and uptime-kuma notifies a change
of state and nothing else. An open incident whose alerts were not seen for two days is tagged
**quiet** in the explorer, and its page has a **Still open?** box for an operator:

- **Re-check now** asks each source that can answer what the alert is right now: OpenObserve
  (its alert's latest evaluation), Alertmanager and uptime-kuma when they have a `url` (their
  own read-only tools). An alert back to normal resolves the incident the usual way. Each
  answer is listed, with what was asked.
- **Close by hand** takes one line of why. Every alert still firing is marked resolved, the
  incident resolves, and the record keeps your name and the line. If an alert fires again, it
  opens an incident again.

![An incident tagged quiet for three days, found with the search box](assets/console/explorer-quiet.webp)

![Still open?: the alerts still firing, whether their source can be asked, and the close form](assets/console/still-open.webp)

![The same incident once closed by hand: who, when and why](assets/console/closed-by-hand.webp)

## The log

Every event Paeon recorded, newest first, 50 a page. Nothing in it is ever edited or removed.

- **Search** finds what the log says (`restart`, a model's name, a host). A search that finds
  little on a page says how many events it looked through, and offers older ones.
- **Kinds**: everything, or only what came in, what code decided, what a model said, what a
  tool read, plans and actions, what left the server, or **What you did**.
- **One incident**: the **only** link on a line narrows the log to that incident; the
  `Incident #3 ×` token above the list removes it.

Each line links to its incident and to its full payload.

![The log: search, kinds, every event newest first](assets/console/log.webp)

![The log narrowed to one incident](assets/console/log-incident.webp)

Everything a person did (rating triage, re-checking, closing, reviewing, answering a plan,
changing a channel or a source) is under **What you did**.

![The log, filtered on what a person did](assets/console/log-what-you-did.webp)

## What it costs

The Spend page shows what the model calls cost over the last 7, 14 or 30 days. At the top,
what was spent today, over the period, how many calls failed, and how often you rated triage
right. **Today** shows each stage's daily budget and what is left of it, and what happens
once it is reached. **By day** stacks the cost of each UTC day by stage, on a labelled scale;
the same numbers are a table one click below. Further down: the period by stage and model,
triage's record, and the latest calls.

![Spend over 30 days: totals, today's budgets, the cost per day by stage](assets/console/spend.webp)

## Actions and autonomy

With actions on, the Actions page has the kill switch, the plans waiting for your answer,
every plan of the last 30 days with its outcome, the catalog and the policies. Autonomy shows,
per alert, action and container, the level in force and the record behind it, and the
promotions that were earned as the line to add to your rules file.

![The Actions page](assets/console/actions.webp)

![The Autonomy page: what was earned, and the track record](assets/console/autonomy.webp)

## Postmortems

The Postmortems page is a table of drafts, newest first: the day, the incident, what broke,
the open action items and the status. Tabs show them all, those **to review**, or those
reviewed. The navigation counts the drafts nobody has reviewed yet.

![The Postmortems page: status tabs and the table of drafts](assets/console/postmortems.webp)

A draft is shown rendered (headings, tables, ticked action items), with its markdown source
one click away; the file itself stays markdown. A `mermaid` block is drawn as a diagram.

**Triage reads a postmortem only once a person has marked it reviewed**; the investigation can
read drafts too, labelled as unreviewed. An operator reviews one from its page:

- **Mark reviewed** sets `status: reviewed` in the file, and nothing else. **Set back to draft**
  undoes it.
- **Edit the text** opens the whole file, front matter included. A front matter that does not
  parse, a status other than `draft` or `reviewed`, or a changed incident number is refused, and
  what you typed stays on the page. A file holding something that looks like a secret is not
  edited in the console: the page would have to show it.
- **Write the prose again** appears on a draft whose model sections are still empty (the model
  failed, or the day's budget was spent). It asks the model again and writes the draft over.
  An invalid answer is also asked again once, with what was wrong with it, when the draft is
  first written.

Each change is in the incident's record, with who made it, and is committed and pushed like
the drafts when `postmortems.git` is set.

![A draft with its Review block: mark reviewed, edit the text](assets/console/postmortem-review.webp)

![The editor: the whole file, front matter included](assets/console/postmortem-edit.webp)

![A rendered postmortem with a sequence diagram](assets/console/postmortem.webp)

## On a phone, in the dark

The console is built phone first, since that is where a notification opens it. On a phone the
explorer's rows take three lines (severity, number and time; the title; the stage track,
triage and cost), the filters fold under one button, and the pages are in a tab bar at the
bottom. It follows your system's light or dark setting unless you use the switch.

![The incident explorer in the dark](assets/console/explorer-dark.webp)

<div class="shots" markdown>
![The explorer on a phone](assets/console/phone-explorer.webp){ width="300" }
![The explorer on a phone, dark](assets/console/phone-explorer-dark.webp){ width="300" }
![An incident on a phone](assets/console/phone-incident.webp){ width="300" }
![What is left to set up, on a phone](assets/console/phone-getting-started.webp){ width="300" }
![A new ntfy channel, on a phone](assets/console/phone-channel-form.webp){ width="300" }
![Reviewing a postmortem, on a phone](assets/console/phone-postmortem-review.webp){ width="300" }
</div>

## What it can and cannot do

What a model said is shown in violet and marked as the model's, what code recorded in plain text:
a verdict is never dressed as a fact. Light or dark follows your system, or the switch you set.

The console reads, apart from what a person decides. An operator says whether triage was right,
re-checks or closes an incident, reviews and edits postmortems, stops automation, and, where the
executor allows it, answers a plan that waits for a Go. With `settings` on an admin manages
sources and channels. It cannot resume what it stopped, it cannot run anything the executor is
not already waiting on, and nothing it configures can carry an approval.

Everything shown is redacted again first, and pages load nothing from another host.
