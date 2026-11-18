# Connect your alerts

Paeon sits next to your alerting, never in its path: your existing notifications stay as they are, and Paeon gets a copy.

| Source | How |
|---|---|
| **uptime-kuma** | Add a notification of type **Webhook**: post URL `http://paeon:8080/ingest/uptime-kuma`, body `application/json`, additional header `{"Authorization": "Bearer <PAEON_INGEST_TOKEN>"}`. Apply it to every monitor. |
| **Alertmanager, Grafana, others** | Point a webhook receiver at `http://paeon:8080/ingest/alertmanager` with the same bearer token. |
| **OpenObserve** | Create a user that can read the `triggers` stream and set the `PAEON_OPENOBSERVE_*` variables. Paeon polls; it never writes to OpenObserve and nothing changes in your alerts. |
| **Planned work** | Announce a deploy, an update run or a backup from the script that does it, so triage knows about it (below). |

```bash
curl -X POST http://paeon:8080/ingest/change -H "Authorization: Bearer $PAEON_INGEST_TOKEN" -H 'Content-Type: application/json' -d '{"kind": "update", "title": "weekly updater", "host": "server-1"}'
```

A pull source that stops answering opens an internal `source_stale` alert, and Paeon can push a
heartbeat to an uptime-kuma push monitor (`PAEON_HEARTBEAT_URL`), so a silent Paeon is noticed.
