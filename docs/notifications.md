# Notifications

Paeon sends three things: the daily **digest**, the **analysis** of an incident, and, with
[actions](acting.md) on, the **approval** of a plan and its result. Each goes to the channels you
choose. A channel is one service: an ntfy topic, a Telegram chat, a mailbox.

Nothing is sent until you configure a channel, and Paeon never replaces the alerts your
monitoring already sends you.

| Service | `type` | Runs on your server | Go / No-go | Section |
|---|---|---|---|---|
| ntfy | `ntfy` | yes | buttons, one tap or confirm page | [ntfy](#ntfy) |
| Telegram | `telegram` | no | buttons | [Telegram](#telegram) |
| Discord | `discord` | no | buttons | [Discord](#discord) |
| Slack | `slack` | no | links | [Slack](#slack) |
| E-mail | `email` | either | links | [E-mail](#e-mail) |
| Gotify | `gotify` | yes | lines of text | [Gotify](#gotify) |
| Pushover | `pushover` | no | links | [Pushover](#pushover) |
| Microsoft Teams | `teams` | no | buttons | [Microsoft Teams](#microsoft-teams) |
| Matrix | `matrix` | either | links | [Matrix](#matrix) |
| Your own receiver | `webhook` | yes | a list in the JSON body | [Webhook](#webhook) |
| Anything else | `apprise` | depends | lines of text | [Apprise](#apprise) |

## Set up a channel

1. **Define it** under `notifiers`, with a name of yours and the keys of its service (below).
2. **Give it its secret** in the environment of the process that sends to it, under the
   variable the channel names (`token_env`, `webhook_env`, ...). `NAME_FILE=/path` works too,
   for Docker secrets.
3. **List it** in the `notify` of each stage that should use it.
4. **Restart** the process: the rules file is read at start.
5. **Try it**: `paeon notify <name>` sends one test message through the channel and says what
   the service answered.

```yaml
notifiers:
  phone:  { type: ntfy, url: https://ntfy.example.org, topic: paeon, token_env: PAEON_NTFY_TOKEN }
  family: { type: telegram, chat_id: "-1001234567890", token_env: PAEON_TELEGRAM_TOKEN }
  ops:    { type: discord, webhook_env: PAEON_DISCORD_WEBHOOK }

digest:        { notify: [ops] }
investigation: { notify: [phone, family] }
actions:       { notify: [phone] }
```

```bash
docker exec paeon paeon notify family
# sent by telegram to -1001234567890
```

The rules that hold for every channel:

- A name is lower-case letters, digits, `-` and `_`, 32 characters at most, and not `ntfy`.
- A stage can list several channels. Each gets the message; one that fails does not keep the
  others from sending, and every delivery is recorded in the event log with the channel's name.
- A secret never goes in the rules file.
- Each process reads only the secrets of its own channels: the core those of `digest` and
  `investigation`, the executor those of `actions`. Run `paeon notify` in the container of the
  process that sends: `paeon` for the digest and the analysis, `paeon-executor` for approvals.
- A listed channel whose secret is missing stops the start, with the name of the variable. A
  channel that no stage lists needs nothing.
- A key that does not exist for the service is refused when the file is loaded.

!!! note "The `ntfy:` block still works"

    `digest.ntfy`, `investigation.ntfy` and `actions.ntfy` are short for one ntfy channel, named
    `ntfy`, and can be used together with `notify`. Nothing has to change in a rules file
    written before channels had names.

The console's **Channels** page lists every channel a stage sends to, what it carries, and how
its deliveries of the last 30 days went.

## What is sent

| Message | Stage | When | Priority | Buttons |
|---|---|---|---|---|
| Digest | `digest` | once a day, if there was an incident | 2 (quiet) | none |
| Was triage right? | `digest` | after the digest, up to `digest.feedback` messages | 1 | Right, Wrong |
| Analysis | `investigation` | when an investigation ends | 3 | Cause is right, Cause is wrong |
| Run this fix? | `actions` | a plan waits for your answer | 4 | Go, No-go |
| Go received, and the like | `actions` | each tap is answered | 3 | none |
| Fix applied / fix failed | `actions` | a plan ran | 3 / 5 | Undo, This was wrong, Stop automation |
| No answer, nothing was run | `actions` | an approval expired | 3 | none |

Priority runs from 1 (quiet) to 5 (urgent), on ntfy's scale; each service maps it to what it
has. Every message also carries a link to the incident's page when `ui.public_url` is set.

## Go and No-go work on every channel

An approval is not a feature of a service. It is a **link**, signed by the executor, that works
once, until it expires, and only for the plan it names ([Acting](acting.md)). A channel only has
to show it. What differs is how:

| Channel | Go / No-go appear as | A tap |
|---|---|---|
| `ntfy` | notification buttons | applies at once (`actions.buttons: one_tap`), or opens the confirm page (`page`) |
| `telegram` | buttons under the message | opens the confirm page |
| `discord` | buttons under the message | opens the confirm page |
| `teams` | buttons on the card | opens the confirm page |
| `slack`, `email`, `matrix`, `pushover` | links in the message | opens the confirm page |
| `gotify`, `apprise` | the links, as lines of text | opens the confirm page, if the app makes the line a link |
| `webhook` | `buttons` in the JSON body | whatever your receiver does with them |

The confirm page is the executor's: it says what the link would do and has one Confirm button.
Opening it changes nothing, so a link preview or a scanner cannot approve a plan.

Three things follow from the approval being a link:

- **The executor must be reachable from where you tap**: `actions.public_url`, on every channel.
- **Every channel listed in `actions.notify` receives the Go link.** Whoever reads that channel
  can approve. List only what you alone read; a team chat is fine for the digest and the
  analysis, and usually not for approvals.
- **With several channels, the first tap decides.** They carry the same links; the second tap
  is told the link was already used.

## The services

No channel adds a dependency, except `apprise`. In each table, a key in bold is required.

### ntfy

A topic on an [ntfy](https://ntfy.sh) server, yours or a public one. It is the one service
whose buttons can apply a link in the background, without opening a page.

**Before you start.** Choose a topic. On your own server, create a user for Paeon that can only
write to it and give yourself read access:

```bash
ntfy user add paeon
ntfy access paeon paeon write-only
ntfy token add paeon          # the token for PAEON_NTFY_TOKEN
```

| Key | Default | |
|---|---|---|
| **`url`** | | The server, for example `https://ntfy.example.org`. |
| **`topic`** | | The topic messages are published to. |
| `token_env` | | Variable with an access token. Leave it out for an open topic. |

```yaml
phone: { type: ntfy, url: https://ntfy.example.org, topic: paeon, token_env: PAEON_NTFY_TOKEN }
```

**What arrives.** A notification with the title, the text, an emoji tag and up to three
buttons (ntfy's limit). A tap on the notification opens the incident's page. With
`actions.buttons: one_tap` (the default) Go is sent by the phone itself and the answer is the
next notification; with `page` it opens the confirm page. The text is cut at 3000 characters.

Use a topic of its own for approvals, with its own token: whoever can read it can press Go.

### Telegram

A bot posts in a chat. Go and No-go are buttons under the message.

**Before you start.**

1. Talk to [@BotFather](https://t.me/BotFather), send `/newbot`, and keep the token it gives.
2. Open a chat with your bot and send it any message, or add it to a group.
3. Find the chat's id: open `https://api.telegram.org/bot<token>/getUpdates` in a browser and
   read `chat.id` in the answer. It is your user id for a private chat and a negative number
   for a group.

| Key | Default | |
|---|---|---|
| **`chat_id`** | | The chat, group or channel the bot posts to. Quote it: `"-1001234567890"`. |
| **`token_env`** | | Variable with the bot's token. |
| `api_url` | `https://api.telegram.org` | Change it only for a Bot API server of your own. |

```yaml
family: { type: telegram, chat_id: "-1001234567890", token_env: PAEON_TELEGRAM_TOKEN }
```

**What arrives.** The title, a blank line and the text, as plain text: nothing in it is read as
formatting. Buttons are a row under the message, with a second row "Details" for the incident's
page. At priority 2 and under (the digest) the message arrives without a sound. The text is cut
at 4096 characters.

A button's link must be an address Telegram accepts: use a public `https` name for
`actions.public_url`, not an internal host name.

### Discord

A webhook of a channel. Go and No-go are link buttons under the message.

**Before you start.** In the channel's settings: **Integrations**, **Webhooks**, **New
webhook**, then **Copy webhook URL**. The URL is the secret: whoever has it can post.

| Key | Default | |
|---|---|---|
| **`webhook_env`** | | Variable with the webhook URL. |
| `username` | the webhook's own | The name the message is posted under. |

```yaml
ops: { type: discord, webhook_env: PAEON_DISCORD_WEBHOOK, username: Paeon }
```

**What arrives.** An embed with the title (a link to the incident's page), the text, and a
colour by priority. Up to five buttons that open their link. Text from an alert cannot mention
anyone or draw a link of its own: mentions are switched off and markdown is escaped. The text
is cut at 4096 characters.

### Slack

An incoming webhook. Go and No-go are links in the message.

**Before you start.** Create an app at [api.slack.com/apps](https://api.slack.com/apps), switch
on **Incoming Webhooks**, add one for the channel and copy its URL
([Slack's guide](https://docs.slack.dev/messaging/sending-messages-using-incoming-webhooks/)).
The URL is the secret.

| Key | Default | |
|---|---|---|
| **`webhook_env`** | | Variable with the webhook URL. |

```yaml
team: { type: slack, webhook_env: PAEON_SLACK_WEBHOOK }
```

**What arrives.** A header with the title, the text, and a line of links: `Go · No-go ·
Details`. They are links and not buttons: Slack sends every button click, even of a button that
only opens a link, to the app's interactivity endpoint, and an app that only has a webhook has
none to answer it. The text is escaped and cut at 3000 characters.

### E-mail

Mail through an SMTP server, with the standard library. Go and No-go are links in the body.

**Before you start.** You need an SMTP server that accepts mail from Paeon: your provider's
submission server with a login, or a relay on your own network.

| Key | Default | |
|---|---|---|
| **`host`** | | The SMTP server. |
| `port` | `587` | 587 with `starttls`, 465 with `tls`. |
| `security` | `starttls` | `starttls`, `tls` from the first byte, or `none` for a relay of your own. |
| `username` | | The login, if the server asks for one. Goes with `password_env`. |
| `password_env` | | Variable with the password. Goes with `username`. |
| **`sender`** | | The From address, a bare address (`paeon@example.org`). |
| **`to`** | | The addresses the mail is sent to, one or more. |

```yaml
mail:
  type: email
  host: smtp.example.org
  username: paeon
  password_env: PAEON_SMTP_PASSWORD
  sender: paeon@example.org
  to: [me@example.org]
```

For a relay that asks for no login:

```yaml
mail: { type: email, host: mail.lan, port: 25, security: none, sender: paeon@lan, to: [me@lan] }
```

**What arrives.** The title as the subject, and a plain and an HTML body, each with the links
(`Go: https://...` in the plain one). From priority 4 the mail is flagged as important. The mail
is marked as generated automatically, so that out-of-office replies leave it alone.

Mail is stored where it lands and often forwarded: think twice before listing a mailbox for
approvals.

### Gotify

An application on a [Gotify](https://gotify.net) server of your own. Gotify has no buttons: the
links are lines of the message.

**Before you start.** In Gotify's web page: **Apps**, **Create application**, and copy its
token.

| Key | Default | |
|---|---|---|
| **`url`** | | The server, without `/message`. |
| **`token_env`** | | Variable with the application's token. |

```yaml
push: { type: gotify, url: https://gotify.example.org, token_env: PAEON_GOTIFY_TOKEN }
```

**What arrives.** The title and the text, then one line per button (`Go: https://...`). A tap
on the notification opens the incident's page. Priority is doubled for Gotify's scale of 0 to
10, so an approval arrives at 8.

### Pushover

[Pushover](https://pushover.net). It has no buttons: the links are in the message.

**Before you start.** Create an application on pushover.net to get its **API token**, and copy
your **user key** (or a group key) from your dashboard.

| Key | Default | |
|---|---|---|
| **`token_env`** | | Variable with the application's API token. |
| **`user_env`** | | Variable with the user or group key. |
| `device` | all | Limit delivery to one of your devices, by its name. |

```yaml
push: { type: pushover, token_env: PAEON_PUSHOVER_TOKEN, user_env: PAEON_PUSHOVER_USER }
```

**What arrives.** The title, the text, and Go and No-go as links under it. The incident's page
is the message's one extra link, "Details". A message is 1024 characters at most: the links are
kept whole and the text is shortened. Priority 1 arrives silently, 4 and 5 as high priority.
Paeon never uses the emergency priority, which repeats until acknowledged.

### Microsoft Teams

A card posted to a Workflows webhook. Go and No-go are buttons on the card.

**Before you start.** In the channel: **Workflows**, the template *Post to a channel when a
webhook request is received*, and copy the URL it gives. The URL is the secret.

| Key | Default | |
|---|---|---|
| **`webhook_env`** | | Variable with the workflow's URL. |

```yaml
team: { type: teams, webhook_env: PAEON_TEAMS_WEBHOOK }
```

**What arrives.** An Adaptive Card with the title in bold, the text, and one button per link:
Go, No-go and Details. The text is not read as markdown.

### Matrix

A message in a room on a Matrix homeserver. Matrix has no buttons: the links are in the
message.

**Before you start.**

1. Create an account for Paeon on your homeserver and join it to the room.
2. Get an access token for that account (in Element: **Settings**, **Help & About**,
   **Advanced**, **Access Token**).
3. Find the room's internal id, which starts with `!` (in Element: the room's **Settings**,
   **Advanced**). It is not the alias that starts with `#`.

| Key | Default | |
|---|---|---|
| **`homeserver`** | | The homeserver's base URL, for example `https://matrix.example.org`. |
| **`room_id`** | | The room's id (`!abcdef:example.org`). Quote it. |
| **`token_env`** | | Variable with an access token of the account that posts. |

```yaml
room:
  type: matrix
  homeserver: https://matrix.example.org
  room_id: "!abcdef:example.org"
  token_env: PAEON_MATRIX_TOKEN
```

**What arrives.** A text message with the title in bold and the links after the text, and a
plain form of the same for clients that show no formatting. Encrypted rooms are not supported:
use a room without encryption.

### Webhook

A `POST` of the message as JSON, for a receiver of your own: n8n, Node-RED, Home Assistant, a
script.

| Key | Default | |
|---|---|---|
| `url` | | Where the message is posted. Give `url` or `url_env`, one of the two. |
| `url_env` | | Variable with the address, when the address holds a secret. |
| `authorization_env` | | Variable with the value of the `Authorization` header. |

```yaml
hook: { type: webhook, url_env: PAEON_HOOK_URL, authorization_env: PAEON_HOOK_AUTHORIZATION }
```

**What arrives.**

```json
{
  "source": "paeon",
  "title": "Incident #7: run this fix?",
  "text": "...",
  "priority": 4,
  "tags": ["wrench"],
  "click": "https://paeon.example.org/ui/incidents/7",
  "buttons": [
    { "label": "Go", "url": "https://paeon-act.example.org/act/..." },
    { "label": "No-go", "url": "https://paeon-act.example.org/act/..." }
  ]
}
```

`click` is `null` without a console address, and `buttons` is empty for a message without any.
A button is applied by a `POST` to its `url`, with no body; a `GET` shows the confirm page. Any
answer under 400 counts as delivered.

### Apprise

[Apprise](https://github.com/caronc/apprise) knows a hundred services (Signal, SMS gateways,
Mattermost, Rocket.Chat, ...). Use it for a service Paeon has no adapter of its own for.

**Before you start.** Apprise is a large library and **not installed by default**:

```bash
pip install 'paeon[apprise]'
```

The published image does not contain it; build your own from it with that one more line if you
need it. A channel of this type without the library stops the start and says so.

| Key | Default | |
|---|---|---|
| **`urls_env`** | | Variable with one or more [Apprise URLs](https://github.com/caronc/apprise/wiki), separated by spaces or new lines. They hold the services' secrets. |

```yaml
more: { type: apprise, urls_env: PAEON_APPRISE_URLS }
```

**What arrives.** The title and the text, with one line per link, sent as plain text to every
URL. From priority 4 it is sent as a warning, otherwise as information. Apprise reports one
result for all its URLs: if one of them fails, the delivery counts as failed.

## When a message does not arrive

`paeon notify <name>` prints what the service answered. The same text is in the event log
(`notification.failed`) and on the console's Channels page.

| It says | It usually means |
|---|---|
| `<service> answered HTTP 401` or `403` | The token, key or webhook URL is wrong, or may not write there. |
| `<service> answered HTTP 404` | The address is wrong: a topic, a chat, a room or a webhook that does not exist (any more). |
| `telegram answered HTTP 400` | The `chat_id` is wrong, the bot was never written to in that chat, or a button's link is not an address Telegram accepts. |
| `<service> answered HTTP 429` | The service is limiting you. The message is not sent again. |
| `<service> request failed: ConnectError` | The server cannot be reached from the container: name, network, port. |
| `<service> request failed: ConnectTimeout` or `ReadTimeout` | It did not answer within 15 seconds. |
| `email failed: SMTPAuthenticationError` | The login or the password is refused. |
| `email failed: SMTPRecipientsRefused` | The server does not accept mail for an address in `to`. |
| `email failed: SSLError` or `SMTPNotSupportedError` | `security` does not match the port: 587 goes with `starttls`, 465 with `tls`. |
| `apprise could not deliver` | At least one Apprise URL failed. Apprise's own log has the reason. |
| `notifiers.<name>: <VARIABLE> (or _FILE) is not set` | The secret is not in this process's environment. |

An error never contains a webhook's address or a token: the service and the status are all it
says. A failed message is not retried, except the digest, which is sent again at the next check
if no channel took it.

## What a channel receives

A channel on someone else's servers (Telegram, Discord, Slack, Teams, Pushover, a mail provider)
receives what you send it: alert titles, the analysis, and, if it is listed under
`actions.notify`, links that approve a plan. See [Privacy and safety](privacy-and-safety.md) and
the [threat model](threat-model.md).

- Text is redacted by the same patterns as everywhere else before it leaves.
- Text from an alert or a log is never read as markup by the service: it cannot mention a
  person, or draw a link that looks like a button.
- When a delivery fails, the event log says the service and the status. It never holds a
  webhook's address or a token.

## Limits

- ntfy, Gotify, e-mail and the webhook were run against real servers, alone and in a full
  approval (alert, Go from one channel, restart, result on all). Telegram, Discord, Slack,
  Teams, Pushover, Matrix and Apprise were tested against the request each service documents,
  not against the live service. Tell us what does not arrive as described.
- There is no answer from inside a chat: a button always goes through the executor's page (or,
  on ntfy, straight to it). Receiving a tap from Telegram or Slack directly is not built.
- One message per event: no editing of an earlier message when a plan is answered.
- Matrix rooms with encryption are not supported.

Every key of every channel, with its type, is also in the
[configuration reference](configuration-reference.md#notifiers).
