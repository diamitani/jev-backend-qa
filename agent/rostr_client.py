"""rostr_client.py — talk to the Rostr v1 API.

Upload path for QA runs: after the local pipeline finishes, `trigger_qa_run`
starts a `backend-qa` agent run on a Rostr project (POST /api/v1/run, SSE),
so the verdict and report live in the project's run history on the Rostr
dashboard — visible to Patrick's whole agent team.

Stdlib only (urllib). Fail-soft everywhere: every method returns a dict
with "ok": False instead of raising on network/API problems, because a
failed upload must never fail the QA run itself.

Auth: the platform's dev mode trusts the caller-supplied project_id, so
no key is needed against the default deployment; pass api_key when the
deployment is Supabase-backed (Bearer token).
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request


class RostrClient:
    def __init__(self, base_url="https://rostr-platform.vercel.app",
                 api_key=None, timeout=600):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def _headers(self, sse=False):
        h = {"Content-Type": "application/json",
             "User-Agent": "jev-backend-qa/2.0"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        if sse:
            h["Accept"] = "text/event-stream"
        return h

    def _read_json(self, resp):
        try:
            return json.loads(resp.read().decode("utf-8"))
        except Exception:
            return None

    def trigger_qa_run(self, project_id, input_text, agent_id="backend-qa",
                       skill="jev-backend-qa", user_id=None):
        """Start a QA run on Rostr and consume its SSE stream.

        Returns {"ok": True, "run_id": ..., "events": n, "final": text}
        or {"ok": False, "error": ...}.
        """
        body = {"project_id": project_id, "agent": agent_id,
                "input": input_text}
        if skill:
            body["skill"] = skill
        if user_id:
            body["user_id"] = user_id
        req = urllib.request.Request(
            self.base_url + "/api/v1/run",
            data=json.dumps(body).encode(),
            headers=self._headers(sse=True), method="POST")
        try:
            resp = urllib.request.urlopen(req, timeout=self.timeout)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            return {"ok": False, "error": f"http_{e.code}: {detail}"}
        except Exception as e:
            return {"ok": False, "error": f"unreachable: {e}"}

        run_id, final, n = None, "", 0
        try:
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                try:
                    ev = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue
                n += 1
                run_id = run_id or ev.get("runId")
                if ev.get("type") in ("done", "result", "reply") and ev.get("text"):
                    final = ev["text"]
                if ev.get("type") == "error":
                    return {"ok": False, "run_id": run_id,
                            "error": ev.get("error", "run_error")}
        except Exception as e:
            return {"ok": False, "run_id": run_id, "error": f"stream: {e}"}
        return {"ok": True, "run_id": run_id, "events": n, "final": final}

    def get_run(self, project_id, run_id):
        """Fetch a stored run record. Returns dict or None."""
        req = urllib.request.Request(
            f"{self.base_url}/api/v1/runs/{run_id}"
            f"?project_id={urllib.parse.quote(project_id)}",
            headers=self._headers())
        try:
            return self._read_json(urllib.request.urlopen(req, timeout=30))
        except Exception:
            return None

    def list_runs(self, project_id, limit=20):
        """List recent run summaries for a project."""
        req = urllib.request.Request(
            f"{self.base_url}/api/v1/runs"
            f"?project_id={urllib.parse.quote(project_id)}",
            headers=self._headers())
        try:
            data = self._read_json(urllib.request.urlopen(req, timeout=30))
        except Exception:
            return []
        return data if isinstance(data, list) else []
