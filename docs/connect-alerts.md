# Connect your alerts

Paeon sits next to your alerting, never in its path: your existing notifications stay as they are, and Paeon gets a copy.

Three sources work with nothing but the ingest token:

| Source | How |
|---|---|
| **uptime-kuma** | Add a notification of type **Webhook**: post URL `http://paeon:8080/ingest/uptime-kuma`, body `application/json`, additional header `{"Authorization": "Bearer <PAEON_INGEST_TOKEN>"}`. Apply it to every monitor. |
| **Alertmanager, and what speaks its webhook** | Point a webhook receiver at `http://paeon:8080/ingest/alertmanager` with the same bearer token. |
| **OpenObserve** | Create a user that can read the `triggers` stream and set the `PAEON_OPENOBSERVE_*` variables. Paeon polls; it never writes to OpenObserve and nothing changes in your alerts. |

Every other service is a **source** you name in the rules file. It posts to `/ingest/<name>`,
or Paeon polls it:

| Service | Alerts arrive | |
|---|---|---|
| Grafana | webhook contact point | [Grafana](sources.md#grafana) |
| Gatus | custom alert | [Gatus](sources.md#gatus) |
| Healthchecks | webhook integration | [Healthchecks](sources.md#healthchecks) |
| Netdata | custom notification | [Netdata](sources.md#netdata) |
| Zabbix | webhook media type | [Zabbix](sources.md#zabbix) |
| Beszel | polled | [Beszel](sources.md#beszel) |
| A second OpenObserve, uptime-kuma or Alertmanager | as above, under a name of yours | [Sources](sources.md) |

```yaml
sources:
  grafana: { type: grafana }
  gatus:   { type: gatus }
```

A source can also give an investigation read-only tools: metrics from Prometheus or
VictoriaMetrics, logs from Loki or VictoriaLogs, monitor states, silences. The
[Sources](sources.md) guide has each service in full: what to set up in it, every key, what
arrives and what can be read.

## Planned work

Announce a deploy, an update run or a backup from the script that does it, so triage knows
about it:

```bash
curl -X POST http://paeon:8080/ingest/change -H "Authorization: Bearer $PAEON_INGEST_TOKEN" -H 'Content-Type: application/json' -d '{"kind": "update", "title": "weekly updater", "host": "server-1"}'
```

A change that takes a while can say when it ended. Give it an `id` of yours when it starts,
then end it under that id when it is done; triage then knows the change is over, not still
running:

```bash
ID="update-$(date +%F)"
curl -X POST http://paeon:8080/ingest/change -H "Authorization: Bearer $PAEON_INGEST_TOKEN" -H 'Content-Type: application/json' -d "{\"id\": \"$ID\", \"kind\": \"update\", \"title\": \"weekly updater\"}"
# … the update runs …
curl -X POST "http://paeon:8080/ingest/change/$ID/end" -H "Authorization: Bearer $PAEON_INGEST_TOKEN"
```

The end takes an optional `{"ended_at": "…"}`; without it, the change ends when the call
arrives. A change sent again with the same `id` replaces the earlier one.

## A silent source, a silent Paeon

A polled source that stops answering opens an internal `source_stale` alert, and Paeon can push a
heartbeat to an uptime-kuma push monitor (`PAEON_HEARTBEAT_URL`), so a silent Paeon is noticed.
