# Backend QA Checklist

The deterministic scanner (`bin/backend_checks.py`) catches the
mechanical stuff. This checklist is the judgment layer — the things a
scan can't prove. Work top to bottom; any "no" or "unknown" becomes a
candidate finding for Jev adjudication.

## 1. Database

- [ ] Every migration applies cleanly, in order, on a **fresh** database
      (not just on the dev DB that already has the tables).
- [ ] Migrations are idempotent (`IF NOT EXISTS` everywhere) or the
      deploy path guarantees single application.
- [ ] Every table has RLS **enabled** and at least one policy — and the
      policies were **read for correctness**, not just existence. The
      classic hole: `USING (true)` on a table that should be
      user-scoped.
- [ ] Service-role usage is confined to server code; anon-key paths
      can't reach admin operations.
- [ ] Hot-path queries (funnel pages, dashboards, list views) have
      indexes; verify with `EXPLAIN`, not vibes.
- [ ] No N+1: list endpoints don't issue one query per row. Batch or
      join.
- [ ] Connection pooling configured (Supabase pooler, PgBouncer) —
      serverless functions must not open direct connections per request.
- [ ] Seeds and fixtures can never run against prod (guarded by env or
      separate scripts, never a default-on seed).
- [ ] Backup / point-in-time recovery confirmed for the prod project.

## 2. API surface

- [ ] Route inventory built fresh from the tree (`app/api/**/route.ts`),
      not from the README. Every route accounted for.
- [ ] Every route: auth present, or explicitly marked **public by
      design** with a reason. Auth is checked before any business logic
      runs.
- [ ] Inputs validated at the boundary (zod or equivalent) — every
      mutation route, no exceptions. Validation errors return a
      consistent shape.
- [ ] Errors: no raw `throw` escaping to the client as a 500 with a
      stack trace. Consistent `{ error: { code, message } }` shape.
- [ ] List endpoints paginated (limit + cursor/offset). No endpoint
      returns an unbounded array.
- [ ] Timeouts on every outbound call (AI gateway, Stripe, third-party
      APIs). A hung upstream must not hang the request forever.
- [ ] Heavy work (video renders, bulk imports, large generations) goes
      through a queue/background job, never the request thread.
- [ ] CORS locked to the real origins. No `*` on credentialed routes.

## 3. AuthN / AuthZ

- [ ] OAuth redirect URIs allowlisted to the real domains (no localhost
      left in prod config).
- [ ] Session/token handling: httpOnly cookies where applicable; no
      tokens in localStorage for server-rendered apps.
- [ ] Authorization (not just authentication): user A cannot read/write
      user B's rows by guessing an ID. Test with two accounts.
- [ ] Webhook endpoints signature-verified (Stripe `constructEvent`,
      etc.) and idempotent on redelivery.

## 4. Env / secrets / config

- [ ] Env inventory reconciled: every `process.env.*` in code exists in
      the schema file and in `.env.example`. Nothing read that isn't
      documented; nothing documented that isn't read.
- [ ] No secrets in the repo, in build logs, or in client bundles
      (verify the built JS, not just the source).
- [ ] Stripe test/live key discipline: test keys never in prod env,
      webhook secrets match the live/test mode of the deployment.
- [ ] AI Gateway routing: app keys vs tooling keys separated; the
      gateway health state in `/api/health` reflects reality.
- [ ] Preview deployments use preview-safe credentials (separate
      Supabase project or schema, test Stripe mode).

## 5. Payments (when Stripe is in the build)

- [ ] Webhook handler covers `checkout.session.completed`,
      `invoice.payment_failed`, `customer.subscription.deleted` at
      minimum — and handles events idempotently.
- [ ] Entitlement checks are server-authoritative (DB or Stripe API),
      never trusting a client-sent plan flag.
- [ ] Price IDs come from env, not hardcoded; changing a price doesn't
      require a code change.

## 6. Observability / ops

- [ ] Structured logging on the critical paths (auth failures, payment
      events, AI gateway errors). Logs carry a request/correlation ID.
- [ ] Alerting exists for: 5xx spike, failed webhooks, DB connection
      exhaustion. (If there's no alerting, that's a finding, not a
      shrug.)
- [ ] `/api/health` reports the true integration state (DB reachable,
      gateway up/down) — the QA intake grounds on it, so a lying health
      endpoint poisons every future QA pass.

## 7. Scale sanity

- [ ] The critical path (signup → pay, or the core loop) was load-tested
      at 10x expected launch traffic, or there's a written reason it
      wasn't.
- [ ] Rate limiting on public endpoints (auth, signup, contact forms,
      any unauthenticated mutation).
- [ ] File uploads: size-capped, type-validated, scanned or sandboxed
      where user content is served back.

## Triage rule

Blocker: data loss, data leak, auth bypass, secret exposure, or money
moving wrong. Warning: anything that degrades under load, on edge
cases, or over time. Minor: hygiene. When in doubt between warning and
blocker, it goes to Jev — the loop decides, not the auditor's gut.
