#!/usr/bin/env python3
"""Jev Backend QA agent (LangChain).

Runs the jev-backend-qa workflow as an autonomous agent:
  deterministic scan -> read the code -> Jev adjudication -> QA report.

The agent judges; it never fixes code and never deploys.

Environment:
  AI_GATEWAY_API_KEY   required — Vercel AI Gateway key (or any
                       OpenAI-compatible provider key)
  AI_GATEWAY_BASE_URL  default https://ai-gateway.vercel.sh/v1
  QA_MODEL             default moonshotai/kimi-k2 (any gateway model id)
  JEV_MODEL            default typesafe-ai/jev

Setup:
  pip install -r requirements.txt
  cd agent && npm install   # only needed for Jev adjudication

Usage:
  python agent/qa_agent.py --repo /path/to/repo
  python agent/qa_agent.py --repo /path/to/repo --report ./qa-report.md --max-findings 25
"""
import argparse
import glob
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CHECKS = os.path.join(HERE, "backend_checks.py")
sys.path.insert(0, HERE)
import pal_verdict  # noqa: E402  -- shared PAL adjudication engine

SYSTEM_PROMPT = """You are the Backend QA agent for Jev-powered builds — an adversarial
backend engineer. You audit a working build the way production will break it.

Workflow:
1. Run the deterministic scan (run_backend_checks) over the full repo tree.
2. If any secret_in_code findings exist: STOP. Report them immediately and end
   the run — a leaked secret blocks everything, no further auditing.
3. Build the API route inventory (list_api_routes). Spot-check the flagged
   files with read_source_file to confirm each finding is real, not a
   tripwire (documented-public routes, test fixtures).
4. Adjudicate every remaining finding with Jev (jev_adjudicate): for each
   finding ask risk (boolean: could this actually break/degrade production?),
   severity (score 0-10 on the given ordered levels), owner (choice), and
   false-positive (boolean). Batch all findings into ONE jev_adjudicate call.
5. Apply the calibrated verdict: BLOCK = p(risk)>=0.75 AND severity>=7 on any
   confirmed finding, or >=3 confirmed findings with p(risk)>=0.60, or any
   secret. WARN = any confirmed finding with 0.40<=p(risk)<0.75. PASS = below.
   False positives are retired with evidence, no waiver needed.
6. Write the QA report (write_qa_report) following references/report-template.md:
   verdict, blockers, warnings, minors, per-area results, Jev scores.
7. End with a short summary: verdict, blockers, what was verified.

Rules: judge, don't fix — never edit the target repo. No secrets in reports
(file + line only, never values). State exactly what was checked and what
remains the user's (keys, migrations, deploy). Keep tool calls tight; don't
re-read files you've already seen."""


def _tool_run_backend_checks(repo_path: str) -> str:
    """Run the deterministic backend checks over the full repo tree.
    Returns the JSON findings payload (summary + findings list)."""
    repo_path = os.path.abspath(os.path.expanduser(repo_path))
    if not os.path.isdir(repo_path):
        return json.dumps({"error": f"not a directory: {repo_path}"})
    p = subprocess.run([sys.executable, CHECKS, "--repo", repo_path],
                       capture_output=True, text=True, timeout=600)
    return p.stdout.strip() or json.dumps({"error": p.stderr[-500:]})


def _tool_list_api_routes(repo_path: str) -> str:
    """List every API route file (App Router) in the repo."""
    repo_path = os.path.abspath(os.path.expanduser(repo_path))
    routes = []
    for pat in ("app/api/**/route.*", "src/app/api/**/route.*"):
        routes += glob.glob(os.path.join(repo_path, pat), recursive=True)
    rel = sorted(os.path.relpath(r, repo_path) for r in routes)
    return json.dumps({"count": len(rel), "routes": rel})


def _tool_read_source_file(path: str, offset: int = 1, limit: int = 120) -> str:
    """Read a slice of a source file. Offset is 1-based."""
    path = os.path.abspath(os.path.expanduser(path))
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError as e:
        return f"cannot read {path}: {e}"
    return "".join(lines[offset - 1:offset - 1 + limit])


def _tool_write_qa_report(path: str, content: str) -> str:
    """Write the QA report markdown file."""
    path = os.path.abspath(os.path.expanduser(path))
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    return f"report written to {path} ({len(content)} chars)"


def _tool_jev_adjudicate(state: str, questions: str) -> str:
    """Send typed questions to Jev for adjudication.
    state: grounding text (stack, production state). questions: JSON dict of
    {id: {type: boolean|score|choice, instructions, criteria?}} following the
    AI SDK EvaluationModelV4 contract.
    Returns Jev's answers JSON."""
    try:
        qs = json.loads(questions)
    except json.JSONDecodeError as e:
        return json.dumps({"error": f"bad questions JSON: {e}"})
    if not isinstance(qs, dict):
        return json.dumps({"error": "questions must be a JSON object keyed by id"})
    result = pal_verdict.run_bridge(
        qs, state=state, bridge_dir=HERE,
        model=os.environ.get("JEV_MODEL", "typesafe-ai/jev"), timeout=600)
    if result is None:
        return json.dumps({"error": "Jev bridge unavailable — node, the `ai` "
                                    "package (npm install in agent/), or "
                                    "AI_GATEWAY_API_KEY is missing"})
    return json.dumps(result.get("answers") or {})


def build_questions(findings):
    """Normalize raw scanner findings and build the PAL battery via the
    shared engine (empty sources — the agent reads code itself)."""
    norm = []
    for i, f in enumerate(findings):
        norm.append({
            "id": f"F{i:03d}",
            "type": f.get("check", "unknown"),
            "file": f.get("file", "?"),
            "line": f.get("line", 0),
            "message": f.get("detail", ""),
            "block": f.get("severity_hint") == "BLOCK",
        })
    return pal_verdict.build_questions(norm, {})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, help="path to the repo to QA")
    ap.add_argument("--report", default="qa-report.md")
    ap.add_argument("--max-findings", type=int, default=25,
                    help="cap findings sent to Jev (cost guard)")
    ap.add_argument("--state", default="",
                    help="production-state grounding text (stack, /api/health)")
    args = ap.parse_args()

    from langchain.agents import create_agent
    from langchain_core.tools import tool
    from langchain_openai import ChatOpenAI

    api_key = os.environ.get("AI_GATEWAY_API_KEY")
    if not api_key:
        sys.exit("AI_GATEWAY_API_KEY is not set — see README.md for where to get one.")
    base_url = os.environ.get("AI_GATEWAY_BASE_URL", "https://ai-gateway.vercel.sh/v1")
    model_name = os.environ.get("QA_MODEL", "moonshotai/kimi-k2")
    llm = ChatOpenAI(model=model_name, api_key=api_key, base_url=base_url)

    @tool
    def run_backend_checks(repo_path: str) -> str:
        """Run the deterministic backend checks over the full repo tree."""
        return _tool_run_backend_checks(repo_path)

    @tool
    def list_api_routes(repo_path: str) -> str:
        """List every API route file in the repo."""
        return _tool_list_api_routes(repo_path)

    @tool
    def read_source_file(path: str, offset: int = 1, limit: int = 120) -> str:
        """Read a slice of a source file (1-based offset)."""
        return _tool_read_source_file(path, offset, limit)

    @tool
    def write_qa_report(path: str, content: str) -> str:
        """Write the QA report markdown file."""
        return _tool_write_qa_report(path, content)

    @tool
    def jev_adjudicate(state: str, questions: str) -> str:
        """Adjudicate findings with Jev. Batch ALL findings into one call."""
        return _tool_jev_adjudicate(state, questions)

    agent = create_agent(llm, [run_backend_checks, list_api_routes,
                               read_source_file, write_qa_report,
                               jev_adjudicate],
                         system_prompt=SYSTEM_PROMPT)

    repo = os.path.abspath(os.path.expanduser(args.repo))
    state = args.state or (
        "Target: full-tree backend QA of " + repo + ". Stack: Next.js App Router + "
        "Supabase + Stripe + Vercel AI Gateway (canonical). Production state: verify "
        "via the app's /api/health before trusting integration claims.")
    task = (
        f"QA the backend of the repo at {repo}.\n"
        f"Write the final report to {os.path.abspath(args.report)}.\n"
        f"Adjudicate at most {args.max_findings} findings with Jev "
        f"(highest severity_hint first); note any truncation in the report.\n"
        f"Grounding state: {state}")
    result = agent.invoke({"messages": [{"role": "user", "content": task}]})
    last = result["messages"][-1]
    print(getattr(last, "content", last))


if __name__ == "__main__":
    main()
