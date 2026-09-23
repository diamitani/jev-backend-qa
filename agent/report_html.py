"""report_html.py — self-contained HTML QA report.

One file, inline CSS, no external assets: safe to attach to a ticket,
drop in a drive, or serve statically. Mirrors the Markdown report's
sections: verdict banner, counts, findings with PAL probabilities,
fix proposals, and the adjudication note.
"""
from __future__ import annotations

import html

CSS = """
:root{--bg:#0e0f13;--card:#171922;--line:#262a38;--txt:#e8eaf0;--mut:#9aa0b4;
--green:#3ddc84;--amber:#ffb224;--red:#ff5c5c;--blue:#6aa8ff}
*{box-sizing:border-box}body{background:var(--bg);color:var(--txt);
font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
margin:0;padding:32px 16px;line-height:1.5}
.wrap{max-width:960px;margin:0 auto}
.banner{border-radius:12px;padding:24px;margin-bottom:24px;border:1px solid var(--line)}
.banner.BLOCK{background:linear-gradient(135deg,#2a1214,#171922);border-color:#5c2226}
.banner.WARN{background:linear-gradient(135deg,#2a2110,#171922);border-color:#5c4a22}
.banner.PASS{background:linear-gradient(135deg,#10241a,#171922);border-color:#1f5c3a}
.banner.UNADJUDICATED{background:linear-gradient(135deg,#1c2030,#171922)}
.banner h1{margin:0 0 4px;font-size:28px;letter-spacing:.5px}
.verdict{font-size:44px;font-weight:800;letter-spacing:2px}
.BLOCK .verdict{color:var(--red)}.WARN .verdict{color:var(--amber)}
.PASS .verdict{color:var(--green)}.UNADJUDICATED .verdict{color:var(--blue)}
.meta{color:var(--mut);font-size:13px;margin-top:8px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;
padding:20px;margin-bottom:16px}
.card h2{margin:0 0 12px;font-size:16px;text-transform:uppercase;
letter-spacing:1px;color:var(--mut)}
table{width:100%;border-collapse:collapse;font-size:14px}
th{text-align:left;color:var(--mut);font-weight:600;padding:8px;
border-bottom:1px solid var(--line);font-size:12px;text-transform:uppercase}
td{padding:10px 8px;border-bottom:1px solid var(--line);vertical-align:top}
tr:last-child td{border-bottom:none}
.pill{display:inline-block;padding:2px 10px;border-radius:999px;font-size:12px;
font-weight:700}
.pill.confirmed{background:#3a1620;color:#ff8a8a}.pill.dismissed{background:#1d2a1f;color:#7ddba0}
.pill.unadjudicated{background:#232838;color:#9fb4ff}
.bar{height:8px;border-radius:4px;background:#262a38;overflow:hidden;min-width:80px}
.bar>i{display:block;height:100%;border-radius:4px}
code{background:#0b0c10;padding:2px 6px;border-radius:4px;font-size:12.5px}
pre{background:#0b0c10;padding:12px;border-radius:8px;overflow-x:auto;font-size:12.5px}
.fix{border-left:3px solid var(--blue);padding:4px 0 4px 14px;margin:12px 0}
.fix h3{margin:0 0 4px;font-size:15px}
.foot{color:var(--mut);font-size:12px;margin-top:24px}
"""

VERDICT_COPY = {
    "BLOCK": "Do not ship. Confirmed high-risk findings need fixes first.",
    "WARN": "Shippable with caution. Review the flagged findings before release.",
    "PASS": "No confirmed risks. Ship it.",
    "UNADJUDICATED": "Jev adjudication was unavailable — treat every finding as a candidate and review manually.",
}


def _pbar(p):
    if p is None:
        return '<span style="color:var(--mut)">n/a</span>'
    pct = max(0, min(100, p * 100))
    color = ("var(--red)" if p >= 0.75 else
             "var(--amber)" if p >= 0.40 else "var(--green)")
    return (f'<div class="bar"><i style="width:{pct:.0f}%;'
            f'background:{color}"></i></div>'
            f'<span style="font-size:12px;color:var(--mut)">{p:.2f}</span>')


def render_html(report):
    """report: dict with target, verdict, confidence, reasons, generated_at,
    counts, findings, fixes, pal_model, unadjudicated_note."""
    v = report.get("verdict", "UNADJUDICATED")
    esc = html.escape
    findings = report.get("findings", [])
    fixes = {fx.get("finding_id"): fx for fx in report.get("fixes", [])
             if isinstance(fx, dict)}

    rows = []
    for f in findings:
        fid = esc(str(f.get("id", "")))
        loc = esc(f"{f.get('file', '?')}:{f.get('line', '?')}")
        msg = esc(str(f.get("message", "")))
        status = f.get("status", "unadjudicated")
        sev = f.get("severity_score")
        sev_txt = f"{sev:.1f}" if isinstance(sev, (int, float)) else "n/a"
        owner = esc(str(f.get("owner") or "—"))
        rat = esc(str(f.get("rationale") or ""))
        fx = fixes.get(f.get("id"))
        fix_html = ""
        if fx:
            fix_html = (f'<div class="fix"><h3>Proposed fix '
                        f'<span class="pill" style="background:#232838;color:#9fb4ff">'
                        f'{esc(str(fx.get("effort", "?")))} effort</span></h3>'
                        f'<div>{esc(str(fx.get("title", "")))}</div>'
                        f'<pre>{esc(str(fx.get("diff_sketch", "")))}</pre>'
                        f'<div style="color:var(--mut);font-size:13px">Verify: '
                        f'{esc(str(fx.get("verify", "")))}</div></div>')
        rows.append(
            f"<tr><td><code>{fid}</code><br><span class=\"pill {status}\">"
            f"{status}</span></td>"
            f"<td><code>{loc}</code><br>{msg}<br>"
            f"<span style=\"color:var(--mut);font-size:12px\">owner: {owner}"
            + (f" — {rat}" if rat else "") + "</span>"
            f"{fix_html}</td>"
            f"<td>{_pbar(f.get('risk_probability'))}</td>"
            f"<td style=\"text-align:center\">{sev_txt}</td></tr>")

    counts = report.get("counts", {})
    reasons = "".join(f"<li>{esc(r)}</li>" for r in report.get("reasons", []))
    pal_note = ("PAL adjudication: Jev "
                f"({esc(str(report.get('pal_model', 'n/a')))}) scored "
                f"{counts.get('adjudicated', 0)} findings; "
                f"{counts.get('dismissed', 0)} dismissed as false positives.")
    if report.get("unadjudicated_note"):
        pal_note = esc(report["unadjudicated_note"])

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Backend QA — {esc(str(report.get('target', '')))} — {v}</title>
<style>{CSS}</style></head>
<body><div class="wrap">
<div class="banner {v}">
  <h1>Backend QA report</h1>
  <div class="verdict">{v}</div>
  <div>{esc(VERDICT_COPY.get(v, ''))}</div>
  <div class="meta">target <code>{esc(str(report.get('target', '')))}</code> ·
  generated {esc(str(report.get('generated_at', '')))} ·
  confidence {report.get('confidence', 0):.0%}</div>
</div>
<div class="card"><h2>Verdict reasons</h2><ul>{reasons}</ul></div>
<div class="card"><h2>Findings ({len(findings)})</h2>
<table><thead><tr><th>Finding</th><th>Detail</th><th style="min-width:110px">p(risk)</th>
<th>Sev</th></tr></thead><tbody>
{''.join(rows) if rows else '<tr><td colspan="4" style="color:var(--mut)">No findings.</td></tr>'}
</tbody></table></div>
<div class="card"><h2>Method</h2><p style="color:var(--mut);font-size:14px">{pal_note}
Deterministic scan first (secrets, auth gaps, unbounded queries, RLS, env);
Jev adjudicates every candidate with a boolean/score/choice/false-positive
question battery in one batched evaluation. The QA agent judges — it never
edits code, pushes, or deploys.</p></div>
<div class="foot">Generated by jev-backend-qa · jev-backend-qa on GitHub</div>
</div></body></html>
"""
