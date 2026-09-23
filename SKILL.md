---
name: "jev-backend-qa"
description: "Runs deep backend QA on any Jev-powered build: deterministic full-tree scans (secrets, auth gaps, unbounded queries, RLS holes), DB/API/env/payment audits, live smoke tests, and Jev-adjudicated pass/warn/block verdicts with a fix-loop. Use when the user says 'QA the backend', 'qa this build', 'is this build safe to ship', or before any launch or major release."
metadata: { "includeInPrompt": true }
---

# Jev Backend QA

## Purpose

The backend QA agent for every Jev-powered build. It audits the whole
backend as one system — data model, migrations, API surface, auth,
env/secrets, payments, integrations, observability — then puts every
finding through the Jev decision loop so the verdict is judged, not
guessed. PAL adjudication (`agent/pal_verdict.py`) turns every finding
into a 4-question battery — real risk (boolean), severity 0–10 (score),
fix owner (choice), false positive (boolean) — evaluated in one batched
Jev call. Calibrated verdict: **BLOCK** = any secret, or p(risk) ≥ 0.75
with severity ≥ 7, or ≥3 confirmed findings with p(risk) ≥ 0.60;
**WARN** = any confirmed finding with 0.40 ≤ p(risk) < 0.75; **PASS** =
below. UNADJUDICATED = Jev unreachable (treat findings as candidates).

This owns **full-build QA**. The jev-loop `qa_gate.py` owns push-time
**diff** QA. The two do not overlap: if a build went through this agent,
the push gate just re-verifies the delta.

## Workflow

1. **Intake.** Get: repo path, commit range (or full tree for a first
   pass), stack manifest, the verified `/api/health` production state
   (refresh it — never trust a stale snapshot), and what QA means for
   this build (pre-launch, regression after a fix loop, or release
   sign-off). Freeze scope: no new features during QA, only QA-driven
   fixes.
2. **Deterministic scan.** Run `bin/backend_checks.py --repo <path>` over
   the full tree. Secrets block immediately — stop and report, do not
   continue auditing around a leaked secret. Everything else becomes
   candidate findings.
3. **DB audit.** Per `references/qa-checklist.md`: migrations apply clean
   in order on a fresh DB, are idempotent, every new table has RLS
   enabled *and* at least one policy, hot-path queries are indexed, no
   unbounded public reads, seeds never touch prod-shaped data.
4. **API contract audit.** Every route in the inventory: auth present
   (or explicitly public by design), inputs validated (zod or
   equivalent), consistent error shape, no `throw` escaping to the
   client unhandled, webhooks signature-verified, list endpoints
   paginated. Build the route inventory fresh — never trust the README's
   version of the API.
5. **Env / secrets / auth audit.** Env inventory reconciled against what
   the code actually reads (`process.env.*` vs schema file). No
   service-role keys in client bundles. OAuth redirect allowlists,
   Stripe test/live discipline, AI Gateway key routing.
6. **Live smoke (when a deploy URL exists).** Hit health + critical API
   paths both unauthenticated and with a test credential. Record status
   codes and latency. A route that exists in code but 404s in prod is a
   finding. Do not hammer — a handful of requests per path.
7. **Jev adjudication.** The PAL engine (`agent/pal_verdict.py`) turns
   every candidate into a 4-question battery (real risk / severity 0–10 /
   fix owner / false positive) batched through one `typesafe-ai/jev`
   `experimental_evaluate` call on the Vercel AI Gateway (same bridge as
   the jev-loop). Calibrated verdict: block = any secret, or p(risk)≥0.75
   + severity≥7, or ≥3 confirmed ≥0.60; warn = any confirmed
   0.40–0.75; pass below. UNADJUDICATED = bridge unreachable.
8. **Report + fix loop.** Write the report per
   `references/report-template.md`. Blockers and warnings go to the
   owning builder; the QA agent **re-verifies the fix, not the claim**
   — re-run the deterministic checks and the affected audit steps, then
   re-adjudicate. Only a clean re-run closes a finding.

## Production surfaces (v2)

- **`jev-qa` CLI** (`agent/cli.py`, pip: `jev-backend-qa[agent]`): one
  command = scan → evidence → PAL adjudication → verdict → fix proposals
  → markdown/HTML/JSON reports → optional Rostr upload. Exit 0 = PASS,
  1 = WARN/UNADJUDICATED, 2 = BLOCK.
- **Rostr-native** (`agent/rostr_client.py`, `agent/rostr_register.py`):
  register `backend-qa` as a first-class Rostr agent, upload finished
  runs to the project's run history. Rostr's Prompt Abstraction Layer
  is the agent-manifest side; this tool's PAL is the probabilistic
  assessment loop — the CLI can invoke both (PAL-compile via
  `/api/v1/pal/compile` before run upload).
- **GitHub Action** (`action.yml`): PR comments with the verdict,
  artifact upload of the HTML report, BLOCK fails the job.
- **`agent/pal_verdict.py`** is the single adjudication engine — the CLI
  and the LangChain agent both use it. Thresholds live there; open an
  issue with `qa-report.json` if a verdict felt wrong.

## Tooling

- `bin/backend_checks.py` — deterministic checks, stdlib only. Usage:
  `python3 bin/backend_checks.py --repo <path> [--json-out f]`.
  Exit 0 = clean, 1 = findings (non-secret), 2 = secret found.
  Checks: secrets in code, throws in routes, unhandled promise chains,
  env vars missing from schema, unbounded Supabase selects, API routes
  without auth, service-role keys in client bundles, tables without RLS
  or policies, non-idempotent migrations, Stripe webhooks without
  signature verification.
- Precision notes: the secret check skips template placeholders
  (`xxx`, `...`, `<redacted>`, `your_`, etc.) so `.env.example` files
  don't block. `route_without_auth` still fires on documented-public
  routes as a tripwire — a human confirms "public by design."
- `~/workspace/jev-loop/` — the PAL -> Jev judgment path. Reuse its
  `pal.py` question shapes and `jev_client.py` bridge; do not fork them.

## Auth

- Jev adjudication needs the Vercel AI Gateway key at QA time. Fetch at
  runtime from the Drive "api keys" sheet via the `hatch_gws_cli` Sheets
  API path (the curl CSV-export path is dead from the sandbox). Never
  print the key, never commit it, shred temp copies immediately.
- Live smoke against prod needs a test credential: ask the user or use
  the Secure Vault flow. Never invent one, never reuse a prod user.

## Operating Rules

1. **Judge, don't fix.** The QA agent reports and re-verifies; it does
   not push fixes to main. Fixes go through the owning builder and the
   normal push path (which re-runs the jev-loop gate).
2. **No deploys.** QA never deploys. A verdict of "pass" is a
   recommendation; the user taps the actual release.
3. **No secrets in reports.** Findings reference file + line, never the
   value. Reports live in the goal workspace, not in the repo.
4. **LLM-agnostic.** Resolve models at runtime via env
   (`WEBAPP_QA_MODEL` → `WEBAPP_DEFAULT_MODEL`); all calls through the
   gateway helper. Never hardcode model names or keys in skill content.
5. **Full tree, not the delta.** Pre-existing backend issues are in
   scope here — that is the point of a build-level QA pass. The diff
   gate stays the push-time guard.
6. **State the verdict exactly as verified.** "Deterministic checks
   clean" is not "prod verified." Name what was checked, what was live,
   and what remains the user's (keys, migrations, deploy tap).
7. When this agent runs inside an agent build system, register it as a
   first-class agent (e.g. `backend-qa`): invoke after the builder handoff
   and before the final review pass.

## Output Contract

- `QA-REPORT-<build>-<date>.md` in the goal workspace per
  `references/report-template.md`: verdict, blockers, warnings, minors,
  per-area results, re-verification log.
- `findings.json` — the raw deterministic findings + Jev adjudication
  scores, for the fix loop and the record.
- Chat reply: verdict in one line, blockers listed, what was verified
  and what is still the user's move. No walls of text.
