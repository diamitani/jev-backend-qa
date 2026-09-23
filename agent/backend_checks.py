#!/usr/bin/env python3
"""backend_checks.py — deterministic full-tree backend checks for Jev-powered builds.

Canonical stack: Next.js App Router + Supabase + Stripe + Vercel AI Gateway.
Stdlib only. Emits JSON findings to stdout.

Exit codes: 0 = clean, 1 = findings (non-secret), 2 = secret found.
Usage: python3 backend_checks.py --repo <path> [--json-out findings.json]
"""
import argparse
import json
import os
import re
import sys

SKIP_DIRS = {".git", ".next", "node_modules", "dist", "build", ".turbo",
             "__pycache__", ".venv", "venv", ".tmp", "tmp"}
SKIP_FILES = {"package-lock.json", "pnpm-lock.yaml", "yarn.lock",
              "bun.lockb", ".env", ".env.local", ".env.production"}
MAX_FILE_BYTES = 1_000_000

# ---------------------------------------------------------------- secrets
SECRET_PATTERNS = [
    (r"sk_live_[A-Za-z0-9]{16,}", "stripe_live_key"),
    (r"sk_test_[A-Za-z0-9]{16,}", "stripe_test_key"),
    (r"whsec_[A-Za-z0-9]{16,}", "stripe_webhook_secret"),
    (r"rk_live_[A-Za-z0-9]{16,}", "stripe_restricted_key"),
    (r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}", "jwt_value"),
    (r"AKIA[0-9A-Z]{16}", "aws_access_key"),
    (r"ghp_[A-Za-z0-9]{20,}", "github_token"),
    (r"github_pat_[A-Za-z0-9_]{20,}", "github_pat"),
    (r"xox[bap]-", "slack_token"),
    (r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", "private_key"),
    (r"AIza[0-9A-Za-z_-]{20,}", "google_api_key"),
    (r"(?i)(service_role['\"]?\s*[:=]\s*['\"]eyJ[A-Za-z0-9_.-]{20,})", "service_role_value"),
]

AUTH_HINTS = re.compile(
    r"getRequestUser|getSession|getServerSession|auth\(\)|withAuth|"
    r"401|unauthorized|Unauthorized|constructEvent|verifySignature|"
    r"signature|PUBLIC_ROUTE|public-by-design|requireAuth|checkAuth",
    re.IGNORECASE)
BOUND_HINTS = re.compile(
    r"\.limit\(|\.range\(|\.eq\(|\.neq\(|\.in\(|\.single\(|"
    r"\.maybeSingle\(|head\s*:")
ENV_RE = re.compile(r"process\.env\.([A-Z0-9_]+)")
SELECT_RE = re.compile(r"\.from\(\s*['\"]([A-Za-z0-9_]+)['\"]\s*\)")
THEN_RE = re.compile(r"\.then\(")
CATCH_RE = re.compile(r"\.catch\(|\.finally\(|await\s")


def strip_line_comments(line, in_block):
    out = []
    i = 0
    while i < len(line):
        if in_block:
            end = line.find("*/", i)
            if end == -1:
                return "", True
            i = end + 2
            in_block = False
        else:
            if line.startswith("//", i):
                break
            if line.startswith("/*", i):
                in_block = True
                i += 2
            else:
                out.append(line[i])
                i += 1
    return "".join(out), in_block


def iter_files(repo):
    for root, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if f in SKIP_FILES:
                continue
            p = os.path.join(root, f)
            try:
                if os.path.getsize(p) > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            yield p


def rel(repo, p):
    return os.path.relpath(p, repo)


def is_route_file(p):
    parts = p.replace(os.sep, "/").split("/")
    return ("app" in parts and "api" in parts
            and os.path.basename(p) in ("route.ts", "route.js"))


def is_client_bundle_file(p, text_head):
    return '"use client"' in text_head or "'use client'" in text_head


def read_text(p):
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


FINDINGS = []


def add(check, path, line, detail, severity_hint):
    FINDINGS.append({
        "check": check,
        "file": path,
        "line": line,
        "detail": detail,
        "severity_hint": severity_hint,  # advisory; Jev adjudicates
    })


# ------------------------------------------------------------------ checks
PLACEHOLDER_RE = re.compile(
    r"xxx|\.\.\.|<|>|your_|example|changeme|placeholder|dummy|todo",
    re.IGNORECASE)


def check_secrets(path, text):
    for i, line in enumerate(text.splitlines(), 1):
        for pat, label in SECRET_PATTERNS:
            for m in re.finditer(pat, line):
                if PLACEHOLDER_RE.search(m.group(0)):
                    continue  # template value, e.g. sk_test_xxxx in .env.example
                add("secret_in_code", path, i,
                    f"possible {label} committed in source", "BLOCK")


def check_route_quality(path, text):
    lines = text.splitlines()
    in_block = False
    has_try = "try" in text and "catch" in text
    saw_then_without_catch = False
    for i, raw in enumerate(lines, 1):
        code, in_block = strip_line_comments(raw, in_block)
        if re.search(r"\bthrow\b", code):
            add("throw_in_route", path, i,
                "throw inside API route — verify it cannot escape as an unhandled 500",
                "WARN")
        if THEN_RE.search(code) and not CATCH_RE.search(code):
            saw_then_without_catch = True
    if saw_then_without_catch and not has_try:
        add("unhandled_promise", path, 0,
            ".then() chain without .catch in a route with no try/catch — "
            "unhandled rejection risk", "WARN")
    if not AUTH_HINTS.search(text):
        add("route_without_auth", path, 0,
            "API route has no visible auth check — confirm it is public by "
            "design or add authentication", "WARN")


def check_supabase_queries(path, text):
    stmts = re.split(r";|\n", text)
    for stmt in stmts:
        m = SELECT_RE.search(stmt)
        if not m:
            continue
        if ".select(" not in stmt:
            continue  # inserts/updates/deletes, not reads
        if not BOUND_HINTS.search(stmt):
            add("unbounded_select", path, 0,
                f"Supabase select on '{m.group(1)}' with no limit/range/filter/"
                "single — unbounded read risk", "WARN")


def check_client_service_key(path, text):
    if re.search(r"SUPABASE_SERVICE_ROLE_KEY|service_role", text):
        add("client_service_key", path, 0,
            "service-role key referenced in a client component — leaks full "
            "DB access to the browser bundle", "BLOCK")


def check_stripe_webhook(path, text):
    if "stripe" in text.lower() and "webhook" in text.lower():
        if not re.search(r"constructEvent|verifySignature|webhooks\.construct",
                         text):
            add("stripe_webhook_no_verify", path, 0,
                "Stripe webhook route without signature verification — "
                "forgeable events", "BLOCK")


def check_sql_migrations(path, text):
    tables = re.findall(
        r"create\s+table\s+(?:if\s+not\s+exists\s+)?([a-zA-Z0-9_\"\.]+)",
        text, re.IGNORECASE)
    for t in tables:
        t = t.strip('"').split(".")[-1]
        seg_start = text.lower().find(t.lower())
        window = text[seg_start:seg_start + 4000].lower()
        if "enable row level security" not in window:
            add("rls_not_enabled", path, 0,
                f"table '{t}' created without ENABLE ROW LEVEL SECURITY",
                "WARN")
        if not re.search(r"create\s+policy", text, re.IGNORECASE):
            add("table_without_policy", path, 0,
                f"migration touches '{t}' but defines no CREATE POLICY — "
                "RLS on with no policies denies everything (or verify)",
                "WARN")
    for m in re.finditer(r"create\s+table\s+(?!if\s+not\s+exists)",
                         text, re.IGNORECASE):
        line = text[:m.start()].count("\n") + 1
        add("migration_not_idempotent", path, line,
            "CREATE TABLE without IF NOT EXISTS — re-runs will fail", "WARN")
    for m in re.finditer(
            r"create\s+(?:unique\s+)?index\s+(?!if\s+not\s+exists)",
            text, re.IGNORECASE):
        line = text[:m.start()].count("\n") + 1
        add("migration_not_idempotent", path, line,
            "CREATE INDEX without IF NOT EXISTS", "WARN")
    for m in re.finditer(
            r"alter\s+table\s+\S+\s+add\s+column\s+(?!if\s+not\s+exists)",
            text, re.IGNORECASE):
        line = text[:m.start()].count("\n") + 1
        add("migration_not_idempotent", path, line,
            "ALTER TABLE ADD COLUMN without IF NOT EXISTS", "WARN")


def collect_env_schema(repo):
    schema = set()
    for cand in ("lib/env.ts", "lib/env.js", "src/lib/env.ts", "env.mjs",
                 "lib/config.ts"):
        p = os.path.join(repo, cand)
        text = read_text(p)
        if text:
            schema |= set(re.findall(r"([A-Z0-9_]{3,})", text))
    example = read_text(os.path.join(repo, ".env.example"))
    if example:
        schema |= set(re.findall(r"^([A-Z0-9_]{3,})\s*=", example,
                                 re.MULTILINE))
    return schema


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--json-out")
    args = ap.parse_args()
    repo = os.path.abspath(args.repo)
    if not os.path.isdir(repo):
        print(f"not a directory: {repo}", file=sys.stderr)
        sys.exit(2)

    env_schema = collect_env_schema(repo)
    env_used = {}

    for p in iter_files(repo):
        text = read_text(p)
        if text is None:
            continue
        r = rel(repo, p)
        check_secrets(r, text)
        low = p.lower()
        if low.endswith((".ts", ".tsx", ".js", ".jsx")):
            if is_route_file(p):
                check_route_quality(r, text)
                check_supabase_queries(r, text)
                check_stripe_webhook(r, text)
            elif "app" in p.replace(os.sep, "/").split("/") or "src" in p:
                check_supabase_queries(r, text)
            if is_client_bundle_file(p, text[:2000]):
                check_client_service_key(r, text)
            for var in ENV_RE.findall(text):
                if var not in ("NODE_ENV",):
                    env_used.setdefault(var, set()).add(r)
        elif low.endswith(".sql"):
            check_sql_migrations(r, text)

    for var, files in sorted(env_used.items()):
        if var not in env_schema:
            add("env_without_schema", sorted(files)[0], 0,
                f"process.env.{var} read in code but missing from env schema "
                f"(.env.example / lib/env) — used in {len(files)} file(s)",
                "WARN")

    out = {
        "repo": repo,
        "findings": FINDINGS,
        "summary": {
            "total": len(FINDINGS),
            "block": sum(1 for f in FINDINGS
                         if f["severity_hint"] == "BLOCK"),
            "warn": sum(1 for f in FINDINGS
                        if f["severity_hint"] == "WARN"),
        },
    }
    payload = json.dumps(out, indent=2)
    if args.json_out:
        with open(args.json_out, "w") as fh:
            fh.write(payload)
    print(payload)
    sys.exit(2 if out["summary"]["block"] else
             1 if out["summary"]["total"] else 0)


if __name__ == "__main__":
    main()
