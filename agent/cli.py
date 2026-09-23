#!/usr/bin/env python3
"""jev-qa — one-command backend QA: download to upload.

    jev-qa ./my-app
    jev-qa ./my-app --suggest-fixes --format both --report-dir ./qa-out
    jev-qa ./my-app --rostr-project artispreneur

Pipeline:
  1. deterministic scan (bin/backend_checks.py)
  2. evidence gathering (read flagged files)
  3. PAL adjudication — Jev scores every finding (boolean/score/choice/
     false-positive) in one batched evaluation
  4. calibrated verdict: BLOCK / WARN / PASS / UNADJUDICATED
  5. fix proposals (LLM, opt-out with --no-suggest-fixes)
  6. reports: qa-report.md + qa-report.html + qa-report.json
  7. optional upload: --rostr-project posts the run to Rostr

Exit codes: 0 PASS · 1 WARN or UNADJUDICATED · 2 BLOCK.

Env:
  AI_GATEWAY_API_KEY  gateway key (PAL adjudication + fix proposals)
  QA_MODEL            model for fix proposals (default moonshotai/kimi-k2)
  JEV_MODEL           model for PAL adjudication (default typesafe-ai/jev)
  ROSTR_URL           Rostr platform base URL (default rostr-platform.vercel.app)
  ROSTR_API_KEY       bearer token when the deployment needs one
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import subprocess
import sys


def _is_ipv6_literal(entry: str) -> bool:
    e = entry.strip()
    if e.startswith("["):
        return True  # [::1], [fd8b:...] — bracketed IPv6
    # bare IPv6 contains '::' or more than one colon; host:port has exactly one
    return "::" in e or e.count(":") > 1


def _sanitize_proxy_env() -> None:
    """Drop IPv6 literals from no_proxy/NO_PROXY.

    Some environments list IPv6 addresses (``::1``, ``[::1]``) in
    no_proxy. The httpx build used by langchain-openai raises
    ``Invalid port`` on any IPv6 literal there. Plain hostnames,
    IPv4 addresses, and host:port entries are kept, so bypass
    behavior is preserved.
    """
    for var in ("no_proxy", "NO_PROXY"):
        raw = os.environ.get(var)
        if not raw:
            continue
        kept = [e for e in raw.split(",") if not _is_ipv6_literal(e)]
        os.environ[var] = ",".join(kept)


_sanitize_proxy_env()

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import pal_verdict
import report_html


def run_scan(target):
    # In-package scanner module — works from a clone and from pip installs.
    scanner = os.path.join(HERE, "backend_checks.py")
    proc = subprocess.run(
        [sys.executable, scanner, "--repo", os.path.abspath(target)],
        capture_output=True, text=True, timeout=600)
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        sys.exit(f"scanner produced no JSON (exit {proc.returncode}): "
                 f"{proc.stderr[:300]}")
    findings = []
    for i, f in enumerate(data.get("findings", []), 1):
        findings.append({
            "id": f"F{i:03d}",
            "type": f.get("check", "unknown"),
            "file": f.get("file", "?"),
            "line": f.get("line", 0),
            "message": f.get("detail", ""),
            "block": f.get("severity_hint") == "BLOCK",
        })
    return findings


def gather_evidence(target, findings, cap=2500):
    sources = {}
    for f in findings:
        p = f["file"]
        if p in sources or p == "?":
            continue
        full = p if os.path.isabs(p) else os.path.join(target, p)
        try:
            with open(full, encoding="utf-8", errors="replace") as fh:
                sources[p] = fh.read()[:cap]
        except OSError:
            sources[p] = ""
    return sources


def render_markdown(report):
    lines = [
        f"# Backend QA report — {report['target']}",
        "",
        f"**Verdict: {report['verdict']}** "
        f"(confidence {report['confidence']:.0%})",
        "",
        f"Generated {report['generated_at']} · "
        f"PAL model `{report['pal_model']}`",
        "",
        "## Reasons",
    ]
    lines += [f"- {r}" for r in report["reasons"]]
    lines.append("")
    lines.append(f"## Findings ({len(report['findings'])})")
    lines.append("")
    lines.append("| ID | Location | p(risk) | Sev | Status | Detail |")
    lines.append("|----|----------|---------|-----|--------|--------|")
    for f in report["findings"]:
        p = f.get("risk_probability")
        s = f.get("severity_score")
        lines.append(
            f"| {f['id']} | `{f['file']}:{f.get('line', '?')}` | "
            f"{p if p is not None else 'n/a'} | "
            f"{s if s is not None else 'n/a'} | {f.get('status')} | "
            f"{f['message'][:100]} |")
    fixes = {fx.get("finding_id"): fx for fx in report.get("fixes", [])
             if isinstance(fx, dict)}
    if fixes:
        lines += ["", "## Proposed fixes", ""]
        for f in report["findings"]:
            fx = fixes.get(f["id"])
            if not fx:
                continue
            lines += [
                f"### {f['id']} — {fx.get('title', '')} "
                f"(effort {fx.get('effort', '?')})",
                "",
                f"File: `{fx.get('file', '')}` "
                f"{fx.get('location', '')}",
                "",
                "```diff",
                str(fx.get("diff_sketch", ""))[:2000],
                "```",
                "",
                f"Verify: {fx.get('verify', '')}",
                "",
            ]
    lines += ["",
              "> QA judges — it never edits code, pushes, or deploys. "
              "Deterministic scan first; Jev adjudicates every candidate "
              "with a boolean/score/choice/false-positive battery."]
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="jev-qa",
        description="Backend QA for Jev-powered builds: scan, PAL-adjudicate, verdict, report, upload.")
    ap.add_argument("target", help="path to the repo to QA")
    ap.add_argument("--report-dir", default="./qa-report",
                    help="where to write qa-report.{md,html,json}")
    ap.add_argument("--format", choices=["md", "html", "both"], default="both")
    ap.add_argument("--suggest-fixes", dest="fixes", action="store_true",
                    default=True, help="LLM fix proposals (default on)")
    ap.add_argument("--no-suggest-fixes", dest="fixes", action="store_false")
    ap.add_argument("--rostr-project",
                    help="upload the run to this Rostr project")
    ap.add_argument("--rostr-url", default=os.environ.get(
        "ROSTR_URL", "https://rostr-platform.vercel.app"))
    ap.add_argument("--jev-model", default=os.environ.get(
        "JEV_MODEL", "typesafe-ai/jev"))
    ap.add_argument("--qa-model", default=os.environ.get(
        "QA_MODEL", "moonshotai/kimi-k2"))
    ap.add_argument("--timeout", type=int, default=600)
    args = ap.parse_args(argv)

    target = os.path.abspath(args.target)
    if not os.path.isdir(target):
        sys.exit(f"not a directory: {target}")

    print(f"[1/6] deterministic scan of {target}")
    findings = run_scan(target)
    print(f"      {len(findings)} candidate finding(s)")

    secret_blocks = [f for f in findings if f["block"]]
    if secret_blocks:
        verdict = {"verdict": "BLOCK", "confidence": 1.0,
                   "reasons": [f"{len(secret_blocks)} deterministic secret "
                               f"block(s) — fail-closed"]}
        for f in findings:
            f.update(status="confirmed" if f["block"] else "unadjudicated",
                     adjudicated_by="deterministic" if f["block"] else "none")
        sources, fixes = {}, []
        pal_model = "deterministic-only"
    else:
        print("[2/6] gathering evidence")
        sources = gather_evidence(target, findings)
        print(f"[3/6] PAL adjudication via Jev ({args.jev_model})")
        pal_verdict.adjudicate(findings, sources, bridge_dir=HERE,
                               jev_model=args.jev_model,
                               timeout=args.timeout)
        n_adj = sum(1 for f in findings
                    if f.get("adjudicated_by") == "jev")
        print(f"      Jev adjudicated {n_adj}/{len(findings)}")
        print("[4/6] verdict")
        verdict = pal_verdict.apply_verdict(findings)
        pal_model = args.jev_model if n_adj else "unavailable"

        fixes = []
        if args.fixes:
            print("[5/6] fix proposals")
            try:
                import fix_advisor
                fixes = fix_advisor.suggest_fixes(
                    findings, sources, model=args.qa_model)
                print(f"      {len(fixes)} proposal(s)")
            except Exception as e:
                print(f"      skipped: {e}")

    report = {
        "target": target,
        "verdict": verdict["verdict"],
        "confidence": verdict["confidence"],
        "reasons": verdict["reasons"],
        "generated_at": datetime.datetime.now(
            datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "pal_model": pal_model,
        "counts": {
            "total": len(findings),
            "confirmed": sum(1 for f in findings
                             if f.get("status") == "confirmed"),
            "dismissed": sum(1 for f in findings
                             if f.get("status") == "dismissed"),
            "adjudicated": sum(1 for f in findings
                               if f.get("adjudicated_by") == "jev"),
        },
        "findings": findings,
        "fixes": fixes,
    }
    if verdict["verdict"] == "UNADJUDICATED":
        report["unadjudicated_note"] = (
            "Jev adjudication was unavailable (no bridge, node, or "
            "AI_GATEWAY_API_KEY). Findings are unadjudicated candidates.")

    print("[6/6] writing reports")
    os.makedirs(args.report_dir, exist_ok=True)
    md = render_markdown(report)
    if args.format in ("md", "both"):
        with open(os.path.join(args.report_dir, "qa-report.md"), "w") as fh:
            fh.write(md)
    if args.format in ("html", "both"):
        with open(os.path.join(args.report_dir, "qa-report.html"), "w") as fh:
            fh.write(report_html.render_html(report))
    with open(os.path.join(args.report_dir, "qa-report.json"), "w") as fh:
        json.dump(report, fh, indent=2)
    print(f"      {os.path.abspath(args.report_dir)}/")

    if args.rostr_project:
        print(f"[upload] Rostr project '{args.rostr_project}'")
        try:
            from rostr_client import RostrClient
            client = RostrClient(
                base_url=args.rostr_url,
                api_key=os.environ.get("ROSTR_API_KEY"))
            summary = (f"Backend QA of {target}: verdict "
                       f"{verdict['verdict']} "
                       f"({report['counts']['confirmed']} confirmed / "
                       f"{report['counts']['total']} findings). "
                       + " ".join(verdict["reasons"][:2])
                       + "\n\nFull report:\n" + md[:6000])
            res = client.trigger_qa_run(args.rostr_project, summary)
            if res.get("ok"):
                print(f"      uploaded — Rostr run {res.get('run_id')}")
            else:
                print(f"      upload failed (report kept locally): "
                      f"{res.get('error')}")
        except Exception as e:
            print(f"      upload failed (report kept locally): {e}")

    v = verdict["verdict"]
    print(f"\nVerdict: {v} (confidence {verdict['confidence']:.0%})")
    for r in verdict["reasons"]:
        print(f"  - {r}")
    sys.exit(2 if v == "BLOCK" else 1 if v in ("WARN", "UNADJUDICATED") else 0)


if __name__ == "__main__":
    main()
