#!/usr/bin/env python3
"""Create a compact, evidence-linked task brief for one independent reviewer."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from orchestrate import choose_model, focus_for, focus_question
from workflow_harness import (acceptance_ids, ensure_local_run_dir, evidence_digest,
                              lexical_absolute, read_state, repo_for_area, required_slots, review_depth)


def changed_paths(repo: Path, base: str | None = None) -> list[str]:
    tracked = subprocess.run(["git", "-C", str(repo), "diff", "--name-only", "--no-renames",
                              base or "HEAD", "-z"], check=True, capture_output=True).stdout
    untracked = subprocess.run(["git", "-C", str(repo), "ls-files", "--others",
                                "--exclude-standard", "-z"], check=True, capture_output=True).stdout
    return sorted({item.decode("utf-8", "surrogateescape") for item in
                   (tracked + untracked).split(b"\0") if item})


def open_findings(run_dir: Path, state: dict, area: str) -> list[dict[str, str]]:
    events = state["events"]
    latest_approval = max((index for index, event in enumerate(events)
                           if event.get("type") == "approval" and event.get("area") == area),
                          default=-1)
    findings = []
    for index, event in enumerate(events):
        if event.get("area") != area:
            continue
        if event.get("type") == "review" and event.get("result") == "changes-required" \
                and index > latest_approval:
            findings.append({"stage": "design-review", "id": f"slot-{event['slot']}",
                             "summary": event.get("finding", "")})
        if event.get("type") == "code-review" and event.get("result") == "findings":
            resolved = any(later.get("type") == "code-review" and later.get("area") == area
                           and later.get("slot") == event["slot"] and later.get("result") == "clear"
                           for later in events[index + 1:])
            if resolved:
                continue
            if event.get("result_json"):
                relative, _ = evidence_digest(run_dir, str(run_dir / event["result_json"]))
                data = json.loads((run_dir / relative).read_text(encoding="utf-8"))
                findings += [{"stage": "code-review", "id": item["id"],
                              "summary": item["observation"]} for item in data["findings"]]
            else:
                findings.append({"stage": "code-review", "id": f"slot-{event['slot']}",
                                 "summary": "review findings await resolution"})
        if event.get("type") == "qa-case" and event.get("result") in {"fail", "not-run"}:
            resolved = any(later.get("type") == "qa-case" and later.get("area") == area
                           and later.get("scenario_id") == event["scenario_id"]
                           and later.get("result") == "pass"
                           for later in events[index + 1:])
            if not resolved:
                findings.append({"stage": "qa", "id": event["scenario_id"],
                                 "summary": event.get("actual", event.get("reason", "unverified"))})
    return findings


def brief(run_dir: Path, stage: str, area: str, slot: int, source: str,
          source_version: str, approval_reference: str, supported: dict[str, list[str]],
          default_model: str, default_effort: str, base: str | None = None) -> dict:
    run_dir = lexical_absolute(run_dir)
    state = read_state(run_dir)
    ensure_local_run_dir(run_dir, repo_for_area(state, area), allow_legacy=True)
    if stage not in {"design-review", "code-review", "qa"} or area not in (
            ["frontend", "backend", "integration"] if state["scope"] == "both" else [state["scope"]]) \
            or slot not in required_slots(state):
        raise ValueError("stage, area, or slot is outside this workflow")
    if not source.strip() or not source_version.strip() or not approval_reference.strip():
        raise ValueError("source, source version, and approval reference are required")
    area_names = ["frontend", "backend"] if area == "integration" else [area]
    criteria = {name: sorted(acceptance_ids(run_dir, name)) for name in area_names}
    path_areas = (["frontend", "backend", "integration"] if area == "integration"
                  else [area])
    changed = []
    for item in path_areas:
        item_base = base or state.get("base_commits", {}).get(item)
        paths = changed_paths(repo_for_area(state, item), item_base)
        changed.extend(f"{item}:{path}" for path in paths)
    return {
        "stage": stage, "area": area, "slot": slot,
        "focus": focus_for(stage, area, slot, review_depth(state)),
        "question": focus_question(stage, area, slot, review_depth(state)),
        "source": source, "source_version": source_version,
        "approval_reference": approval_reference,
        "design_files": [str(run_dir / f"{name}-design.md") for name in area_names],
        "plan_files": [str(run_dir / "verification-plan.json"), str(run_dir / "qa-plan.json")],
        "acceptance_ids": criteria,
        "changed_paths": changed,
        "open_findings": open_findings(run_dir, state, area),
        "evidence_dir": str(run_dir),
        "policies": ["read-only review", f"{len(required_slots(state))} distinct agent ID(s)",
                     "expand context when dependencies or ownership are uncertain",
                     "never convert failed or unexecuted evidence to PASS"],
        "spawn": {"preferred": {"fork_turns": "none", "context_mode": "isolated"},
                  "fallback": {"fork_turns": "all", "context_mode": "full-fallback",
                               "rule": "If isolation is unsupported, record why and provide full context; keep distinct agents."}},
        "selection": choose_model(stage, slot, supported, default_model, default_effort),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--stage", choices=("design-review", "code-review", "qa"), required=True)
    parser.add_argument("--area", choices=("frontend", "backend", "integration"), required=True)
    parser.add_argument("--slot", type=int, choices=(1, 2, 3), required=True)
    parser.add_argument("--source", required=True, help="Path or conversation reference to original request")
    parser.add_argument("--source-version", required=True)
    parser.add_argument("--approval-reference", required=True, help="Latest approval or 'none'")
    parser.add_argument("--supported-models", type=Path, required=True,
                        help="JSON mapping of host model IDs to supported effort values")
    parser.add_argument("--default-model", required=True)
    parser.add_argument("--default-effort", required=True)
    parser.add_argument("--base", help="Git commit before the implementation changes")
    args = parser.parse_args()
    try:
        supported = json.loads(args.supported_models.read_text(encoding="utf-8"))
        print(json.dumps(brief(args.run_dir, args.stage, args.area, args.slot,
                               args.source, args.source_version, args.approval_reference,
                               supported, args.default_model, args.default_effort, args.base),
                         ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError,
            json.JSONDecodeError) as error:
        print(f"review_brief: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
