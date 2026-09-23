# jev-backend-qa

The backend QA agent for Jev-powered builds — and the strictest one you can
`pip install`. It audits the whole backend as one system (database,
migrations, API surface, auth, env/secrets, payments, observability), then
puts every finding through the **PAL adjudication loop**: Jev scores each
finding's real production risk as a probability, and a calibrated verdict
follows — **BLOCK**, **WARN**, **PASS**. Judged, not guessed.

**PAL** here is the *probabilistic assessment loop* (boolean / score /
choice / false-positive questions → `experimental_evaluate` → per-finding
p(risk)). Not to be confused with Rostr's Prompt Abstraction Layer — the
two compose: this tool can upload its runs to Rostr (below).

## Download → upload in one command

```bash
pip install jev-backend-qa[agent]     # or: git clone https://github.com/diamitani/jev-backend-qa.git
cd agent && npm install && cd ..      # node bridge for Jev adjudication

export AI_GATEWAY_API_KEY="your-key-here"
jev-qa ./my-app
```

That single command runs the full pipeline and exits with the verdict:

| Exit | Verdict | Meaning |
|------|---------|---------|
| 0 | **PASS** | No confirmed risks. Ship it. |
| 1 | **WARN** / **UNADJUDICATED** | Review flagged findings (or Jev was unreachable — findings are candidates). |
| 2 | **BLOCK** | Do not ship. Confirmed high-risk findings. |

Output lands in `./qa-report/`: `qa-report.md`, `qa-report.html`
(self-contained — attach it to a ticket), and `qa-report.json` (machine
readable).

### What the pipeline does

1. **Deterministic scan** — 10 stdlib-only checks over the full tree:
   secrets in code, auth gaps, unbounded Supabase selects, RLS holes,
   env/schema drift, Stripe webhook verification, and more. A secret in
   code blocks immediately, fail-closed, no LLM needed.
2. **Evidence gathering** — reads the flagged files so Jev judges code,
   not summaries.
3. **PAL adjudication** — every finding becomes a 4-question battery
   (real risk? severity 0–10? who owns it? false positive?) evaluated in
   **one** batched Jev call. Per-finding p(risk) in the report.
4. **Calibrated verdict** — BLOCK at p(risk)≥0.75 + severity≥7 (or 3+
   findings ≥0.60, or any secret); WARN at 0.40–0.75; PASS below.
5. **Fix proposals** — concrete file/line/diff-sketch suggestions for
   every confirmed finding (opt out with `--no-suggest-fixes`). The agent
   judges; it never edits your repo.
6. **Upload** — `--rostr-project <id>` posts the run to your Rostr
   dashboard; the GitHub Action (below) posts the verdict on the PR.

```bash
jev-qa ./my-app --suggest-fixes --format both --report-dir ./qa-out
jev-qa ./my-app --rostr-project artispreneur
jev-qa ./my-app --jev-model typesafe-ai/jev --qa-model moonshotai/kimi-k2
```

### Scanner only (no key, no node)

```bash
python3 bin/backend_checks.py --repo /path/to/repo
# or, from the package: python3 -c "from agent import backend_checks" ...
```

Exit 0 = clean, 1 = findings, 2 = secret found. Stdlib only — runs
anywhere.

### The LangChain agent

For interactive/autonomous runs instead of the one-shot CLI:

```bash
pip install -r requirements.txt
python3 agent/qa_agent.py --repo /path/to/repo --report ./qa-report.md
```

Five tools (scan, route inventory, read code, Jev adjudication, report
writer) driven by any gateway model. Same PAL engine, same verdict
contract as the CLI.

### Rostr integration

`jev-qa` is Rostr-native: with `--rostr-project <id>`, the finished run —
verdict, reasons, full report — is posted to your Rostr project's run
history via the v1 API, so the whole agent team sees QA status on the
dashboard.

One-time setup per platform checkout:

```bash
python3 agent/rostr_register.py /path/to/rostr-platform artispreneur
# or --all for every project, then redeploy the platform
```

Needs the `backend-qa` agent registered (the script does it) — the CLI
degrades gracefully when Rostr is unreachable; the local report is always
the source of truth.

### GitHub Action — QA gate on every PR

```yaml
- uses: diami tani/jev-backend-qa@main
  with:
    gateway-key: ${{ secrets.AI_GATEWAY_API_KEY }}
```

Posts the verdict as a PR comment (🛑/⚠️/✅), uploads the HTML report as
a workflow artifact, and fails the job on BLOCK. See `action.yml`. This
repo dogfoods it (`.github/workflows/qa.yml`).

### Where to get an API key

The agent talks to any OpenAI-compatible endpoint. Easiest path:

1. Go to [vercel.com](https://vercel.com) → your account → **AI Gateway** →
   create an API key.
2. `export AI_GATEWAY_API_KEY="..."` and run.

Without a key, everything except PAL adjudication and fix proposals still
works — findings are reported as unadjudicated candidates.

## Upload / contribute

- **Fix a bug or add a check:** fork → branch → PR. Small, focused PRs
  merge fastest. The scanner targets Next.js + Supabase + Stripe today;
  `agent/backend_checks.py` is stdlib-only Python — PRs for other stacks
  welcome.
- **Calibrate:** if a verdict felt wrong, open an issue with the
  `qa-report.json` — thresholds live in `agent/pal_verdict.py`.
- **Direct push access:** open an issue and ask.

## Repo layout

| Path | What |
|---|---|
| `SKILL.md` | The agent playbook (also a drop-in skill for agent harnesses) |
| `agent/cli.py` | `jev-qa` one-command pipeline |
| `agent/pal_verdict.py` | PAL adjudication engine + calibrated verdict |
| `agent/fix_advisor.py` | LLM fix proposals for confirmed findings |
| `agent/report_html.py` | Self-contained HTML report |
| `agent/rostr_client.py` | Rostr v1 API client (run upload) |
| `agent/rostr_register.py` | Register the backend-qa agent on Rostr projects |
| `agent/qa_agent.py` | The LangChain agent (same engine, interactive) |
| `agent/jev_bridge.mjs` | Node bridge: typed Jev evaluations via the AI SDK |
| `agent/backend_checks.py` | Deterministic full-tree checks, stdlib only |
| `bin/backend_checks.py` | Shim → the packaged scanner |
| `action.yml` | GitHub Action: PR verdict comments + BLOCK gate |
| `references/` | Audit checklist, report template, PAL question templates |

## Verdict semantics

- **BLOCK** — any secret in code; or any confirmed finding with
  p(risk)≥0.75 and severity≥7; or 3+ confirmed findings ≥0.60. Do not ship.
- **WARN** — any confirmed finding with 0.40≤p(risk)<0.75. Fix or waive
  in writing before launch.
- **PASS** — below thresholds. A pass is a recommendation, not a release.
- **UNADJUDICATED** — Jev was unreachable; treat findings as candidates.

Reports never contain secret values — file + line references only.

## License

MIT — see `LICENSE`.
