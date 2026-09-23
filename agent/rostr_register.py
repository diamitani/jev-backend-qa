#!/usr/bin/env python3
"""rostr_register.py — register the backend-qa agent on a Rostr project.

The Rostr v1 API has no agent-registration endpoint, so this patches the
platform checkout directly: it appends the `backend-qa` agent entry to
projects/<id>/project.json and installs the skill text the platform loads
at run time (skills/jev-backend-qa/SKILL.md).

Usage:
    python3 agent/rostr_register.py /path/to/rostr-platform <project-id>
    python3 agent/rostr_register.py /path/to/rostr-platform artispreneur --all

--all registers on every project in the checkout.

After registering, redeploy the platform (it deploys via CLI, not
GitHub-connected):
    cd /path/to/rostr-platform && npx -y vercel@latest --prod

Then QA runs can be uploaded with:
    jev-qa ./my-app --rostr-project artispreneur
"""
import json
import os
import shutil
import sys

AGENT_ENTRY = {
    "id": "backend-qa",
    "name": "Backend QA",
    "systemPrompt": (
        "You are the Backend QA agent for this Rostr project. You judge "
        "builds; you never fix code, push, or deploy. Follow the "
        "jev-backend-qa skill exactly: run the deterministic scan, gather "
        "evidence on flagged code, adjudicate every finding through the "
        "PAL question battery (boolean/score/choice/false-positive) with "
        "Jev, then deliver a BLOCK / WARN / PASS verdict with calibrated "
        "probabilities and concrete fix proposals. Be terse and specific: "
        "file, line, and p(risk) for every confirmed finding."
    ),
    "skills": ["jev-backend-qa"],
    "model": "moonshotai/kimi-k2",
}


def register_one(platform_dir, project_id, skill_src):
    proj_file = os.path.join(platform_dir, "projects", project_id,
                             "project.json")
    if not os.path.exists(proj_file):
        return f"SKIP {project_id}: no projects/{project_id}/project.json"
    with open(proj_file, encoding="utf-8") as fh:
        project = json.load(fh)
    agents = project.setdefault("agents", [])
    if any(a.get("id") == "backend-qa" for a in agents):
        agent_note = "agent already registered"
    else:
        agents.append(dict(AGENT_ENTRY))
        with open(proj_file, "w", encoding="utf-8") as fh:
            json.dump(project, fh, indent=2)
            fh.write("\n")
        agent_note = "agent added"

    skill_dir = os.path.join(platform_dir, "skills", "jev-backend-qa")
    os.makedirs(skill_dir, exist_ok=True)
    shutil.copyfile(skill_src, os.path.join(skill_dir, "SKILL.md"))
    return f"OK {project_id}: {agent_note}; skill installed"


def main():
    if len(sys.argv) < 3:
        sys.exit("usage: rostr_register.py <platform-dir> <project-id|--all>")
    platform_dir = os.path.abspath(sys.argv[1])
    target = sys.argv[2]
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    skill_src = os.path.join(repo_root, "SKILL.md")
    if not os.path.exists(skill_src):
        sys.exit(f"SKILL.md not found at {skill_src}")

    if target == "--all":
        projects_dir = os.path.join(platform_dir, "projects")
        ids = sorted(d for d in os.listdir(projects_dir)
                     if os.path.isdir(os.path.join(projects_dir, d)))
    else:
        ids = [target]
    for pid in ids:
        print(register_one(platform_dir, pid, skill_src))
    print("\nNext: redeploy the platform so the agent goes live:")
    print(f"  cd {platform_dir} && npx -y vercel@latest --prod")


if __name__ == "__main__":
    main()
