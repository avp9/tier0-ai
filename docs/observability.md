# Logs and tracing a request

## What the app logs

One JSON object per line on stdout (`LOG_FORMAT=json`, the default outside local development; `LOG_LEVEL` defaults to `INFO`). Container Apps ships stdout to the Log Analytics workspace `log-tier0` (table `ContainerAppConsoleLogs_CL`, column `Log_s`).

| Field | Meaning |
|---|---|
| `ts`, `level`, `logger`, `message` | When, how serious, which module, what happened |
| `request_id` | One per HTTP request. A caller's `X-Request-ID` is reused only if it is 8 to 64 letters, digits or hyphens; anything else is replaced. Every response, including a 500, carries `X-Request-ID` |
| `thread_id` | The conversation, on every line written while serving that conversation |
| `user_id` | The signed-in user's internal ID (never the email) |
| per-event fields | For example `intent`, `workflow`, `awaiting`, `sources`, `ticket_key`, `message_chars`, `duration_ms`, `status`, `outcome` |

Events: `request` (one per request, path only, no query string; health checks are skipped), `chat turn`, `ticket submission created|already_created|unknown|unavailable|rejected`, `login succeeded|failed`, rate-limit hits, `conversation not found or not owned`, and unhandled errors with a stack trace.

## What is never logged

- **What anyone said**: the employee's message, the agent's reply, ticket titles and descriptions. A chat turn logs only the *length* of the message. Tests send a marker string through the API at DEBUG level and fail if it appears anywhere.
- **Email addresses**: masked to the first letter and the domain (`a***@buzztech.example`). This is applied to every message, field and stack trace, so an address inside an exception text is masked too.
- **Secrets**: API keys (`sk-...`), `Bearer` tokens, `password=`/`token=`/`api_key=` assignments and passwords inside connection URLs are replaced.

Limits: masking is pattern-based, so it is a safety net and not a guarantee. The real protection is that the code does not pass message text to the logger. Names and other personal data that are not email addresses are not detected. Third-party libraries' own log lines also go through the masking, but at `INFO` they write little.

## Following one request

Take the `X-Request-ID` header from a failing response (the browser's network tab shows it), then in Log Analytics:

```kusto
ContainerAppConsoleLogs_CL
| where ContainerAppName_s == "ca-tier0-prod"
| extend e = parse_json(Log_s)
| where e.request_id == "<id from the response>"
| project TimeGenerated, level = e.level, message = e.message, e.path, e.status, e.exception
| order by TimeGenerated asc
```

All activity in one conversation: replace the filter with `e.thread_id == "<thread id>"`. Slow chat turns:

```kusto
ContainerAppConsoleLogs_CL
| extend e = parse_json(Log_s)
| where e.message == "chat turn"
| summarize p50 = percentile(todouble(e.duration_ms), 50), p95 = percentile(todouble(e.duration_ms), 95) by bin(TimeGenerated, 1h)
```

These queries have not been run against a live workspace (nothing is deployed yet); the column names are the standard Container Apps ones, so expect to adjust them on the first real use.

## Locally

`LOG_FORMAT=auto` (the default) prints readable text lines when `APP_ENV` is `local` or `e2e` and JSON otherwise; the compose stack sets JSON. The text format prints readable lines with the same fields. Set `LOG_LEVEL=DEBUG` for more detail; the no-message-text rule holds at every level.
