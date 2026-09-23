# QA Report — <build name>

**Date:** <YYYY-MM-DD HH:MM TZ>
**Repo / range:** <path, commit range or "full tree">
**QA agent:** jev-backend-qa
**Production state grounded on:** <`/api/health` snapshot time + key states>

## Verdict: PASS / WARN / BLOCK

One line: why.

## Blockers (must fix before ship)

1. **[check]** `file:line` — what it is, why it blocks, who owns the fix.
   - Re-verification: <pending / verified clean on re-run>

## Warnings (fix or waive in writing)

1. **[check]** `file:line` — what it is, the risk, who owns it.

## Minors (backlog)

1. ...

## Per-area results

| Area | Result | Notes |
|---|---|---|
| Deterministic scan | <clean / N findings> | <link to findings.json> |
| Database | <pass/warn/block> | migrations, RLS, indexes |
| API contract | <pass/warn/block> | auth, validation, errors |
| Env / secrets | <pass/warn/block> | inventory reconciliation |
| Payments | <pass/warn/block / n/a> | webhooks, entitlements |
| Live smoke | <pass/warn/block / not run> | endpoints hit, latency |
| Observability | <pass/warn/block> | logging, alerting, health |

## Jev adjudication

- Findings sent to Jev: N. Blocked: N (list). Warned: N. Passed: N.
- Thresholds: block = severity >=75 + risk >=0.7, or any secret;
  warn = 40–74. Latency notes if the gateway was slow.

## What was verified vs what remains yours

- Verified: <what the agent actually checked and how>
- Still yours: <keys, migrations to apply, the deploy tap, anything the
  agent couldn't reach>

## Fix-loop log

| Date | Finding | Fix commit | Re-verified |
|---|---|---|---|
| | | | |
