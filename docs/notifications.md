# Notifications

Paeon sends three things: the daily **digest**, the **analysis** of an incident, and, with
[actions](acting.md) on, the **approval** of a plan and its result. Each goes to the channels you
choose. A channel is one service: an ntfy topic, a Telegram chat, a mailbox.

Nothing is sent until you configure a channel, and Paeon never replaces the alerts your
monitoring already sends you.

## How it is configured

Channels are defined once, by name, under `notifiers`. Each stage lists the names it sends to in
`notify`:

```yaml
notifiers:
  phone:  { type: ntfy, url: https://ntfy.example.org, topic: paeon, token_env: PAEON_NTFY_TOKEN }
  family: { type: telegram, chat_id: "-1001234567890", token_env: PAEON_TELEGRAM_TOKEN }
  ops:    { type: discord, webhook_env: PAEON_DISCORD_WEBHOOK }

digest:        { notify: [ops] }
investigation: { notify: [phone, family] }
actions:       { notify: [phone] }
```

- A name is yours to choose: lower-case letters, digits, `-` and `_`.
- A stage can list several channels. Each gets the message; one that fails does not keep the
  others from sending, and every delivery is recorded in the event log with the channel's name.
- A secret never goes in the file. The channel names the environment variable that holds it
  (`token_env`, `webhook_env`, ...), and `NAME_FILE=/path` works too, for Docker secrets.
- Each process reads only the secrets of its own channels: the core those of `digest` and
  `investigation`, the executor those of `actions`. A listed channel whose secret is missing
  stops the start, with the name of the variable.

!!! note "The `ntfy:` block still works"

    `digest.ntfy`, `investigation.ntfy` and `actions.ntfy` are short for one ntfy channel, and
    can be used together with `notify`. Nothing has to change in a rules file written before
    channels had names.

The console's **Channels** page lists every channel a stage sends to, what it carries, and how
its deliveries of the last 30 days went.

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

No channel adds a dependency, except `apprise`.

### ntfy

```yaml
phone: { type: ntfy, url: https://ntfy.example.org, topic: paeon, token_env: PAEON_NTFY_TOKEN }
```

`url` is the server, `topic` the topic. `token_env` is optional (a topic can be open) and
strongly advised: give the sender a token that can only write, and yourself read access. ntfy is
the one service whose buttons can apply a link in the background.

### Telegram

```yaml
family: { type: telegram, chat_id: "-1001234567890", token_env: PAEON_TELEGRAM_TOKEN }
```

1. Create a bot with [@BotFather](https://t.me/BotFather) and keep its token.
2. Start a chat with the bot, or add it to a group.
3. `chat_id` is that chat's id: your user id for a private chat, a negative number for a group.

Messages are sent as plain text. At digest priority they arrive without a sound. Telegram
refuses a button whose link is not a public `http(s)` address, so `public_url` must be one.

### Discord

```yaml
ops: { type: discord, webhook_env: PAEON_DISCORD_WEBHOOK, username: Paeon }
```

In the channel's settings: **Integrations**, **Webhooks**, **New webhook**, **Copy webhook URL**.
The URL is the secret. The message is an embed; text from an alert cannot mention anyone or draw
a link of its own.

### Slack

```yaml
team: { type: slack, webhook_env: PAEON_SLACK_WEBHOOK }
```

Create an app with an [incoming webhook](https://docs.slack.dev/messaging/sending-messages-using-incoming-webhooks/)
for the channel; the URL is the secret. Links are in the text, not buttons: a Slack button
expects an app that answers the click, and a webhook has none.

### E-mail

```yaml
mail:
  type: email
  host: smtp.example.org
  port: 587                 # the default
  security: starttls        # the default; `tls` for port 465, `none` for a relay of your own
  username: paeon
  password_env: PAEON_SMTP_PASSWORD
  sender: paeon@example.org
  to: [me@example.org]
```

`username` and `password_env` go together, and can both be left out for a relay that asks for
neither. The mail has a plain and an HTML body, each with the links; from priority 4 it is
flagged as important.

### Webhook

```yaml
hook: { type: webhook, url_env: PAEON_HOOK_URL, authorization_env: PAEON_HOOK_AUTHORIZATION }
```

For a receiver of your own (n8n, Node-RED, a script). Use `url` in place of `url_env` when the
address holds no secret; `authorization_env` is the value of the `Authorization` header and is
optional. The body:

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

`priority` runs from 1 (quiet) to 5 (urgent). A button is applied by a `POST` to its `url`, with
no body; a `GET` shows the confirm page.

### Gotify

```yaml
push: { type: gotify, url: https://gotify.example.org, token_env: PAEON_GOTIFY_TOKEN }
```

`token_env` is the token of an **application** you create on the server. Gotify has no buttons:
the links are lines of the message, and a tap on the notification opens the incident's page.

### Pushover

```yaml
push: { type: pushover, token_env: PAEON_PUSHOVER_TOKEN, user_env: PAEON_PUSHOVER_USER }
```

`token_env` is your application's API token, `user_env` your user or group key; `device` limits
it to one device. A message is 1024 characters at most: the links are kept whole and the text is
shortened. Paeon never uses the emergency priority, which repeats until acknowledged.

### Microsoft Teams

```yaml
team: { type: teams, webhook_env: PAEON_TEAMS_WEBHOOK }
```

In the channel: **Workflows**, the template *Post to a channel when a webhook request is
received*, and copy the URL it gives. The message is an Adaptive Card with buttons that open
the links.

### Matrix

```yaml
room:
  type: matrix
  homeserver: https://matrix.example.org
  room_id: "!abcdef:example.org"
  token_env: PAEON_MATRIX_TOKEN
```

`token_env` is an access token of the account that posts, which must have joined the room.
`room_id` is the internal id (it starts with `!`), not the alias.

### Apprise, for everything else

```yaml
more: { type: apprise, urls_env: PAEON_APPRISE_URLS }
```

[Apprise](https://github.com/caronc/apprise) knows a hundred services (Signal, SMS gateways,
Mattermost, Rocket.Chat, ...). `PAEON_APPRISE_URLS` holds one or more of its URLs, separated by
spaces or new lines.

It is a large library and **not installed by default**:

```bash
pip install 'paeon[apprise]'
```

The published image does not contain it; build your own from it with that one more line if you
need it. A channel of this type without the library stops the start and says so.

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

- The adapters were tested against the request each service documents, not against the live
  services. Tell us what does not arrive as described.
- There is no answer from inside a chat: a button always goes through the executor's page (or,
  on ntfy, straight to it). Receiving a tap from Telegram or Slack directly is not built.
- One message per event: no editing of an earlier message when a plan is answered.
