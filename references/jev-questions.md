# Jev Adjudication Questions

PAL-compile each deterministic candidate into these typed questions and
batch them through `typesafe-ai/jev` on the Vercel AI Gateway (same
bridge as `~/workspace/jev-loop/jev_client.py`). Ground every question
in the verified `/api/health` production state and the real stack —
never in a hardcoded "everything is fine" prior.

## Q1 — production risk (boolean)

> "Given this finding in `<file>` on a `<stack>` app where `<prod state,
> e.g. Supabase ON, gateway OFF, Stripe live>`, could it plausibly cause
> data loss, data exposure, an auth bypass, or incorrect money movement
> in production as deployed? Answer true/false with one sentence of
> reasoning."

Maps to the loop's **risk** probability (true ≈ ≥0.7).

## Q2 — severity (score 0–4 → scaled 0–100)

> "Score the severity of this finding from 0 (cosmetic) to 4
> (ship-blocking: exploitable or data-destroying in prod), considering
> exploitability, blast radius, and whether the failure is silent.
> Return the integer and one sentence."

Scale: score × 25 = severity. Block needs ≥75 (i.e. 3–4) **and** Q1 true.
40–74 warns.

## Q3 — fix owner (choice)

> "Who owns the fix: `web-app-builder` (app code), `tech-stack-connector`
> (infra/env/integrations), `agent-builder` (agent internals), or
> `human-only` (keys, billing, console moves)? One choice."

The fix loop routes blockers/warnings to the owner; QA re-verifies the
fix, not the claim.

## Q4 — false-positive check (boolean, for tripwires)

> "Is this finding a false positive — e.g. a route intentionally public,
> a select bounded elsewhere, a test fixture? Answer true/false with the
> evidence."

Tripwires from the scanner (public-by-design routes, test fixtures) get
this question first; a `true` retires the finding with the evidence
logged, no human waiver needed.

## Verdict assembly

- Any secret (deterministic) → **BLOCK**, no Jev vote needed.
- Else: block = severity ≥75 AND risk ≥0.7. Warn = 40–74. Pass = below.
- Report per-finding scores in `findings.json` under `jev: {risk,
  severity, owner, fp}` so the fix loop and the record are auditable.
