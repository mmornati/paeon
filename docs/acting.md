# Acting: approvals, runbooks and earned autonomy

By default Paeon advises. This page is about letting it restart a container for you: what you set
up, what happens on an incident, and how an action earns the right to run without asking.

Read the [threat model](threat-model.md) too. The short version: the part of Paeon that reads
alerts and talks to models (the core) is the part an attacker can reach, so it is given no way to
act and no way to approve. A second process, the executor, has both, and believes only its own
copy of the rules file and a link you tapped.

## What you need

| | |
|---|---|
| A second process | `paeon executor`: the same image with another command. It has its own database, its own port (8081) and its own secrets. |
| Two secrets | `PAEON_EXECUTOR_TOKEN`, shared by the core and the executor, and `PAEON_SIGNING_KEY`, for the executor alone. They must differ. |
| A Docker proxy that can write | For the executor only: start, stop and restart of containers, and nothing else. With `tecnativa/docker-socket-proxy` that is `POST: 1`, `CONTAINERS: 0` and the `ALLOW_*` switches: `POST` with `CONTAINERS` on would also allow creating and deleting containers. The executor then lists and inspects through a read-only proxy (`docker_read_url`). |
| An ntfy topic for approvals | With a token of its own and read access for you alone. A one-tap approval is exactly as strong as this channel. |
| One public path | `https://<somewhere>/act/` routed to the executor, **without** single sign-on: the phone's button has no session, the signed link is the proof. Route nothing else there. |

[examples/compose.yaml](../examples/compose.yaml) has all of it. Generate the secrets with:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

## The rules file

Both processes read the same format, and the `actions` section is the only part the executor
uses. Give the executor **its own copy**, mounted read-only from git. If the two copies differ,
the executor's wins: a plan the core proposes from a catalog entry the executor does not have is
refused and recorded.

```yaml
actions:
  enabled: true
  executor_url: http://paeon-executor:8081
  public_url: https://paeon-act.example.org
  docker_url: http://dockerproxy-act:2375
  docker_read_url: http://dockerproxy:2375
  lock_dir: /locks
  ntfy: { url: https://ntfy.example.org, topic: incidents, token_env: PAEON_ACTIONS_NTFY_TOKEN }
  catalog:
    restart_container:
      targets: [worker, "media-*"]
      deny: [media-db]
      verify: { wait_seconds: 15, timeout_seconds: 120, http: "http://worker:8080/healthz" }
  policies:
    - { alert: "worker_stalled", action: restart_container, target: worker, level: approve }
```

### The catalog

Six actions exist, and the file cannot add one:

| Action | Does | Pre-check | Inverse (Undo, rollback) |
|---|---|---|---|
| `restart_container` | `POST /containers/{id}/restart` | the container exists | none |
| `start_container` | `POST /containers/{id}/start` | it exists and is not running | stop |
| `unpause_container` | `POST /containers/{id}/unpause` | it is paused | pause |
| `reattach_network_alias` | reconnects the container to its networks with its compose service name as alias | it is a compose service, and a network misses the alias | none |
| `coolify_restart` | `POST /api/v1/applications/{uuid}/restart` (or `services`) on Coolify | the resource answers | none |
| `config_change` | one value of one YAML file, as a pull request | see below | close the pull request |

For `reattach_network_alias` the write proxy also needs `NETWORKS: 1`, which with `POST: 1`
allows creating and removing networks: add it only if you use the action. It reconnects only the
networks that lost the alias, and keeps the aliases they had.

For `coolify_restart`, name the resources it may restart; a plan uses your name, never a uuid:

```yaml
actions:
  coolify:
    url: http://coolify:8000
    token_env: PAEON_COOLIFY_TOKEN        # an API token that can deploy; executor only
    resources:
      media: { uuid: abc123def, kind: service }
  catalog:
    coolify_restart: { targets: [media] }
```

`targets` and `deny` are name patterns. Leave out, or deny, what must never be restarted by a
machine: your identity provider, the reverse proxy, the tunnel you reach the server through, your
observability stack, and Paeon itself.

`verify` is how the executor decides a step worked: after `wait_seconds`, and for up to
`timeout_seconds`, the container must be running, not restarting, healthy if it has a health
check, and `http` must answer below 400 if you gave one. Give one where you can: "running" is a
weak signal. A container with a health check says `starting` until its first check has run, so
`timeout_seconds` must be longer than the check's interval, or the step fails for being slow.

### Policies

The level of one (alert, action, container), first match wins, `default_level` (`suggest`)
otherwise. The alert is its title, as a regular expression that must match all of it.

| Level | What happens |
|---|---|
| `observe` | The plan is recorded. Nothing is sent, nothing runs. |
| `suggest` | The plan is in the analysis you receive. No button. |
| `approve` | You get **Go** and **No-go**. Nothing runs without Go. |
| `auto` | It runs, is verified, and then you are told, with **Undo** where there is an inverse, **This was wrong**, and **Stop automation**. |

A plan with several steps runs at the most careful level of its steps. Whatever the policies say,
a plan is held at `approve` when the incident has more than one alert class, when the
investigation's confidence is under `autonomy.min_confidence`, or when a plan already ran for
that incident; and at `suggest` when more than `max_per_hour` plans ran in the last hour, or
when the executor has no way to ask (no signing key, no `public_url`, no ntfy topic). The reason
is recorded with the plan and shown on the incident's page.

Start with `approve` on one alert and one container.

## What happens on an incident

1. **A plan is made.** Either the investigator's analysis has a step that is exactly one catalog
   action on one container (the model fills two fields; code checks them against the catalog
   before the analysis is accepted), or a runbook matched the alert when the incident opened.
2. **The executor checks it** against its own catalog and policies, and decides the level.
3. **You are asked** (`approve`). The notification says what and why, and lists the steps in
   plain words. Go and No-go are signed links that work once, for `approval_minutes`, for this
   exact plan. Opening one in a browser shows what it would do and a Confirm button; only the
   POST behind the button applies. A new plan for the same incident, the incident resolving, or
   the kill switch kills them. With no answer nothing happens, and one reminder says so.
   A button gives no sign of its own that the tap arrived, so every tap is answered: "Go
   received" at once, before the result, and for a button that no longer works a message
   saying why it did nothing (once per button). A forged link gets no answer.
   That answer is a notification, so it is only as prompt as your phone's push. If yours is
   slow or unreliable (ntfy on iOS with a self-hosted server depends on a relay), set
   `actions.buttons: page`: a button then opens that page in the browser, Confirm applies it,
   and the page says what happened. Two taps instead of one, and an answer on screen.
4. **It runs.** Per step: lock the container, check the world is still as assumed, apply,
   verify. A failed step is rolled back where there is an inverse, and the plan stops.
5. **You are told** what was done and whether it verified. A failure is a high-priority
   notification: the incident is yours.
6. **It is recorded.** Every decision is an event in the executor's journal, mirrored into the
   incident's record, the console and the postmortem's automation review.

## Configuration changes

Restarting a container that runs out of memory fixes tonight. The durable fix is a line in a
compose file, and that is configuration: Paeon never applies it. `config_change` turns it into a
pull request on your deployment repository, and merging is your approval, given on the forge.

```yaml
actions:
  repository:
    path: /repo                       # a clone that is Paeon's own: nothing else uses it
    base: main
    identity_file: /run/secrets/paeon_deploy_key
    known_hosts_file: /config/known_hosts
    github: { repo: you/deployment, token_env: PAEON_GITHUB_TOKEN }
    files:
      media:
        path: stacks/media/compose.yaml
        keys: ["services.*.mem_limit", "services.*.deploy.resources.limits.memory"]
        values: "[0-9]+[mMgG]"        # what a new value must look like
  catalog:
    config_change: { targets: [media] }
  policies:
    - { action: config_change, level: approve }
```

What it can do is narrow on purpose:

- one file you named, one key that matches a pattern you wrote (`*` is one name, it does not
  cross a dot), one value that matches your expression;
- the key must already exist and hold a single value. Nothing is added, removed or moved;
- only the value's own characters change. Comments, order, quoting and layout stay, and the
  file is parsed again afterwards: if anything else differs, the change is dropped;
- the branch is `paeon/incident-<id>-<hash>`, always cut from the remote's base. Nothing is
  merged, and nothing is deployed.

At `approve` or `auto` the pull request is opened without a Go on your phone: it is the request
for approval. At `suggest` or `observe` nothing is pushed. Give the deploy key write access to
that one repository, and the token the right to open pull requests on it and nothing else.
Without `github` the branch is pushed and the notification says so.

A change and a plan of actions for the same incident live side by side: the restart waits for
your Go while the pull request waits for your review.

## Runbooks

A runbook is a known alert and its known fix. A hit skips triage and investigation: no model is
called, which makes the frequent cases fast, free and the same every time.

```yaml
actions:
  runbooks:
    - name: worker-stalled
      match: { source: uptime-kuma, title: "worker_stalled" }
      mode: escalate
      steps:
        - { action: restart_container, target: worker }
        - { action: restart_container, target: vpn }
```

With `mode: escalate` (the default) the steps are a ladder: the first one that verifies ends the
plan. `sequence` runs them all and stops at the first failure. Each step must be allowed by the
catalog, or the file is refused at start. The runbook waits `settle_seconds` first, in case the
alert clears by itself. If no step fixes it, or the executor cannot be reached for ten minutes,
the incident is handed to the investigator.

A runbook's plan goes through the same policies as any other: write `approve` for it until you
trust it. At `observe` or `suggest` the plan is recorded and nothing runs, so the incident is not
handled: it goes to the investigator like any other, and the record shows what the runbook would
have done.

### Migrating a watchdog script

If a script of yours already repairs a service (a soft fix, then a restart, then a restart of
what it depends on), it maps onto a runbook:

1. Make the script's condition an alert that reaches Paeon (an uptime-kuma push monitor that the
   service's own health loop feeds works well).
2. Write the restarts as the runbook's steps, in the script's order, and the script's health test
   as `verify.http` in the catalog.
3. Run both for a while: have the script take the same lock, so they never act together:

   ```bash
   flock --nonblock /var/lock/paeon/worker.lock ./repair-worker.sh
   ```

4. Compare what the runbook would have done (`level: observe`) with what the script did. Then
   move the policy to `approve`, and retire the script when you no longer need it.

A fix that is not a restart (reloading a configuration, re-adding a network alias) has no action
in the catalog. Keep the script for those.

## Earning `auto`

Paeon never changes a policy. It keeps the record, and shows you when one has been earned:

- **Promotion is proposed.** After `autonomy.promote_after` (5) approved, verified runs in a row
  of one action on one container for one alert, within `window_days` (90), the Autonomy page and
  `paeon autonomy` print the line to add:

  ```yaml
  - { alert: "worker_stalled", action: restart_container, target: "worker", level: auto }
  ```

  Put it above the line it overrides, commit, and deploy the rules file to the executor.
- **Demotion is automatic.** One failed verification, one Undo or one "This was wrong" sends the
  tuple back to `approve`, whatever the file says, until you lift it:

  ```bash
  docker compose exec paeon-executor paeon reinstate worker_stalled restart_container worker
  ```

  A restart cannot be undone, which is why "This was wrong" exists: say it when the restart ran
  and should not have.

## The kill switch

Everything drops to `observe`, plans that wait are cancelled, and a Go already given is not run.
Four ways to engage it: **Stop automation** on any result notification, the button on the
console's Actions page, `PAEON_KILL_SWITCH=1` in the executor's environment, or:

```bash
docker compose exec paeon-executor paeon stop
```

Only you can release it, next to the executor. Neither the console nor the core can:

```bash
docker compose exec paeon-executor paeon resume
```

## What it cannot do

- Act on anything but containers of the Docker host its proxy reaches and the Coolify resources
  you named.
- Apply a change to configuration, delete data, touch a secret or a firewall. A change to a
  value is proposed as a pull request; anything else is said in words, for you.
- Approve from the console. A configuration change is approved by merging it on the forge;
  nothing riskier exists.
- Re-run the alert's own query to confirm a fix. The container's health decides; the incident
  resolving, minutes or an hour later, is the confirmation, and it is in the record.
