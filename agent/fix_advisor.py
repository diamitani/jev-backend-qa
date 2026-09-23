"""fix_advisor.py — concrete fix proposals for confirmed findings.

The QA agent judges; it never applies fixes. But a verdict without a path
forward is half a tool, so for every *confirmed* finding the advisor asks
the model for a concrete, file-and-line-scoped fix sketch: what to change,
a unified-diff-style sketch, and how to verify.

Lazy-imports langchain — raises RuntimeError when it is not installed, so the
scanner and PAL verdict always work without it.
"""
from __future__ import annotations

import json
import os

SYSTEM = """You are a senior backend engineer writing fix proposals for QA
findings. For each finding, propose ONE concrete fix: the exact file and
location, a minimal unified-diff-style sketch (not necessarily
apply-ready, but precise), and a one-line verification step. Be terse.
Never invent APIs that do not exist in the shown code. Output JSON only:
a list of {"finding_id": ..., "title": ..., "file": ..., "location": ...,
"diff_sketch": ..., "verify": ..., "effort": "S|M|L"}."""


def suggest_fixes(findings, sources, model=None, max_findings=12):
    """Propose file/line fix sketches for confirmed findings.

    Raises RuntimeError when langchain is not installed, so the caller
    can print an actionable skip message. Returns a list of
    {finding_id, file, line, proposal} dicts ([] when no confirmed
    findings).
    """
    confirmed = [f for f in findings if f.get("status") == "confirmed"]
    if not confirmed:
        return []
    try:
        from langchain_openai import ChatOpenAI
    except ImportError:
        raise RuntimeError("langchain not installed — "
                           "pip install 'jev-backend-qa[agent]' for fix proposals")

    model = model or os.environ.get("QA_MODEL", "moonshotai/kimi-k2")
    llm = ChatOpenAI(
        model=model,
        base_url="https://ai-gateway.vercel.sh/v1",
        api_key=os.environ.get("AI_GATEWAY_API_KEY", "none"),
        temperature=0.2,
    )

    batch = confirmed[:max_findings]
    items = []
    for f in batch:
        ev = (sources.get(f["file"], "") or "")[:2000]
        items.append(
            f"--- FINDING {f['id']} [{f['type']}] "
            f"{f['file']}:{f.get('line', '?')}\n"
            f"{f['message']}\n"
            f"p(risk)={f.get('risk_probability')} "
            f"severity={f.get('severity_score')} "
            f"owner={f.get('owner')}\nCode:\n{ev}")
    prompt = ("Propose fixes for these backend QA findings:\n\n"
              + "\n\n".join(items))

    try:
        resp = llm.invoke([
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": prompt},
        ])
        text = resp.content if isinstance(resp.content, str) else str(resp.content)
    except Exception:
        return []

    # Tolerate code-fenced JSON.
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        t = t.rsplit("```", 1)[0]
    try:
        proposals = json.loads(t.strip())
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(proposals, list):
        return []
    valid_ids = {f["id"] for f in batch}
    return [p for p in proposals
            if isinstance(p, dict) and p.get("finding_id") in valid_ids]
