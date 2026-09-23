"""pal_verdict.py — the PAL adjudication engine.

PAL here is the Jev *probabilistic assessment loop* (not Rostr's Prompt
Abstraction Layer): every deterministic finding becomes a small battery of
typed questions — boolean (is this a real risk?), score (severity 0-10),
choice (who owns it?), boolean (false positive?) — and the whole battery
is evaluated in ONE batched `experimental_evaluate` call through the Jev
bridge. Question/answer shapes follow the AI SDK's EvaluationModelV4
contract exactly (questions are a Record<id, question>; boolean answers
carry P(true); score answers are fractional positions on the level
scale).

The engine turns Jev's per-finding probabilities into a calibrated
ship verdict:

    BLOCK  p(risk) >= 0.75 and severity >= 7 on any confirmed finding,
           or >= 3 confirmed findings with p(risk) >= 0.60,
           or any deterministic secret block (fail-closed, no LLM needed)
    WARN   any confirmed finding with 0.40 <= p(risk) < 0.75
    PASS   everything else (or every finding dismissed as false positive)

Thresholds mirror the jev-loop contract in references/jev-questions.md
(block >= 75 + 0.7, warn 40-74).

Fail-soft: if the Jev bridge is unavailable (no node, no `ai` package,
no gateway key), findings keep status "unadjudicated" and the verdict is
UNADJUDICATED unless a deterministic secret block forces BLOCK. The
report always says which findings Jev actually saw.
"""
from __future__ import annotations

import json
import os
import subprocess

BLOCK_P = 0.75
BLOCK_SEV = 7.0
BLOCK_COUNT = 3
BLOCK_COUNT_P = 0.60
WARN_P = 0.40
FP_P = 0.50

DEFAULT_STATE = (
    "Backend QA of a Jev-powered production build "
    "(Next.js + Supabase + Stripe + Vercel AI Gateway). "
    "Judge production risk honestly: a finding is only real if it is "
    "reachable in production and not mitigated elsewhere."
)

# 10 ordered levels (TypeSafe's max) -> score answers land in [0, 9];
# mapped linearly onto the 0-10 severity scale the verdict uses.
SEV_LEVELS = [
    "negligible (0)", "trivial (1)", "minor (2)", "moderate (3)",
    "notable (4)", "serious (5)", "high (6)", "severe (7)",
    "critical (8)", "catastrophic (9)",
]
_SEV_SCALE = 10.0 / 9.0

OWNERS = {
    "backend": "application code (routes, middleware, validation)",
    "database": "schema, RLS policies, queries",
    "devops": "env config, secrets handling, deployment",
    "payments": "Stripe / billing code paths",
    "frontend": "client-side code only",
}


def build_questions(findings, sources):
    """Turn scanner findings into the PAL question battery.

    findings: list of {id, type, file, line, message, block}
    sources:  dict of path -> source excerpt shown to Jev as evidence
    Returns a dict {question_id: question} in EvaluationModelV4 shape.
    """
    questions = {}
    for f in findings:
        fid = f["id"]
        ev = (sources.get(f["file"], "") or "")[:1500]
        ctx = (f"Finding {fid} [{f['type']}] at {f['file']}:{f.get('line', '?')}: "
               f"{f['message']}\nCode evidence:\n{ev}")
        questions[f"{fid}::risk"] = {
            "type": "boolean",
            "instructions": (
                "Is this a REAL, exploitable production backend risk — "
                "reachable in production and not mitigated elsewhere? "
                f"{ctx}"),
            "criteria": {
                "true": "genuine exploitable risk in production",
                "false": "not a real risk — test/fixture/docs/placeholder, "
                         "or mitigated elsewhere",
            },
        }
        questions[f"{fid}::severity"] = {
            "type": "score",
            "instructions": f"If exploited, how severe is the impact? {ctx}",
            "criteria": SEV_LEVELS,
        }
        questions[f"{fid}::owner"] = {
            "type": "choice",
            "instructions": f"Which team owns the fix? {ctx}",
            "criteria": OWNERS,
        }
        questions[f"{fid}::fp"] = {
            "type": "boolean",
            "instructions": (
                "Is this finding a FALSE POSITIVE — test code, docs, "
                "example/placeholder value, or dead code? Cite the "
                f"evidence. {ctx}"),
            "criteria": {
                "true": "false positive — do not act on it",
                "false": "genuine finding — worth fixing",
            },
        }
    return questions


def run_bridge(questions, state=None, bridge_dir=None, model=None,
               timeout=600):
    """Run one batched experimental_evaluate via the node Jev bridge.

    Returns the parsed result dict (with "answers"), or None when the
    bridge is unavailable. Never raises for bridge/environment problems —
    the caller treats None as "unadjudicated".
    """
    bridge_dir = bridge_dir or os.path.dirname(os.path.abspath(__file__))
    bridge = os.path.join(bridge_dir, "jev_bridge.mjs")
    node_modules = os.path.join(bridge_dir, "node_modules")
    if not (os.path.exists(bridge) and os.path.isdir(node_modules)):
        return None
    if not os.environ.get("AI_GATEWAY_API_KEY"):
        return None
    payload = {
        "model": model or "typesafe-ai/jev",
        "state": state or DEFAULT_STATE,
        "questions": questions,
    }
    try:
        proc = subprocess.run(
            ["node", bridge], input=json.dumps(payload).encode(),
            capture_output=True, timeout=timeout, cwd=bridge_dir)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    try:
        out = json.loads(proc.stdout.decode())
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not out.get("ok"):
        return None
    return out.get("result") or {}


def _num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def adjudicate(findings, sources, bridge_dir=None, jev_model=None,
               state=None, timeout=600):
    """Enrich findings with Jev probabilities. Returns the findings list
    with added keys: risk_probability, severity_score, false_positive,
    owner, status, adjudicated_by."""
    questions = build_questions(findings, sources)
    result = run_bridge(questions, state=state, bridge_dir=bridge_dir,
                        model=jev_model, timeout=timeout)
    answers = (result or {}).get("answers", {})

    for f in findings:
        fid = f["id"]
        if result is None:
            f.update(adjudicated_by="none", status="unadjudicated",
                     risk_probability=None, severity_score=None,
                     false_positive=None, owner=None)
            continue
        risk = answers.get(f"{fid}::risk", {})
        sev = answers.get(f"{fid}::severity", {})
        own = answers.get(f"{fid}::owner", {})
        fp = answers.get(f"{fid}::fp", {})
        # boolean answer: probability IS P(true) per the contract.
        p = _num(risk.get("probability"))
        # score answer: fractional position on [0, 9] -> 0-10 severity.
        s = _num(sev.get("score")) * _SEV_SCALE
        is_fp = _num(fp.get("probability")) >= FP_P
        f.update(
            adjudicated_by="jev",
            risk_probability=round(max(0.0, min(1.0, p)), 3),
            severity_score=round(max(0.0, min(10.0, s)), 2),
            false_positive=is_fp,
            owner=own.get("choice"),
            status="dismissed" if is_fp else "confirmed",
        )
    return findings


def apply_verdict(findings):
    """Calibrated ship verdict over adjudicated findings.

    Returns {"verdict": "BLOCK"|"WARN"|"PASS"|"UNADJUDICATED",
             "confidence": 0..1, "reasons": [...]}.
    """
    reasons = []

    secret_blocks = [f for f in findings if f.get("block")]
    if secret_blocks:
        reasons.append(f"{len(secret_blocks)} deterministic secret block(s) "
                       f"— fail-closed, no adjudication needed")
        return {"verdict": "BLOCK", "confidence": 1.0, "reasons": reasons}

    adjudicated = [f for f in findings
                   if f.get("status") in ("confirmed", "dismissed")]
    if not adjudicated and findings:
        reasons.append("Jev adjudication unavailable — findings are "
                       "unadjudicated candidates, treat as WARN")
        return {"verdict": "UNADJUDICATED", "confidence": 0.5,
                "reasons": reasons}
    if not findings:
        return {"verdict": "PASS", "confidence": 1.0,
                "reasons": ["no findings"]}

    confirmed = [f for f in adjudicated if f["status"] == "confirmed"]
    dismissed = len(adjudicated) - len(confirmed)

    blockers = [f for f in confirmed
                if (f.get("risk_probability") or 0) >= BLOCK_P
                and (f.get("severity_score") or 0) >= BLOCK_SEV]
    if blockers:
        reasons.append(f"{len(blockers)} confirmed finding(s) with "
                       f"p(risk)>={BLOCK_P} and severity>={BLOCK_SEV}: "
                       + ", ".join(b["id"] for b in blockers[:5]))
        return {"verdict": "BLOCK", "confidence": 0.9, "reasons": reasons}

    hot = [f for f in confirmed
           if (f.get("risk_probability") or 0) >= BLOCK_COUNT_P]
    if len(hot) >= BLOCK_COUNT:
        reasons.append(f"{len(hot)} confirmed findings with "
                       f"p(risk)>={BLOCK_COUNT_P} — pattern, not incident")
        return {"verdict": "BLOCK", "confidence": 0.8, "reasons": reasons}

    warns = [f for f in confirmed
             if (f.get("risk_probability") or 0) >= WARN_P]
    if warns:
        reasons.append(f"{len(warns)} confirmed finding(s) in WARN band "
                       f"[{WARN_P},{BLOCK_P}): "
                       + ", ".join(w["id"] for w in warns[:5]))
        conf = 0.7 if dismissed == 0 else 0.6
        return {"verdict": "WARN", "confidence": conf, "reasons": reasons}

    if dismissed and not confirmed:
        reasons.append(f"all {dismissed} finding(s) dismissed by Jev as "
                       f"false positives")
    else:
        reasons.append("no confirmed finding reaches the WARN band")
    return {"verdict": "PASS", "confidence": 0.85, "reasons": reasons}
