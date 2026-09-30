#!/usr/bin/env python3
"""Create a compact, evidence-linked task brief for one independent reviewer."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

from orchestrate import choose_model, focus_for, focus_question
from render_result import requires_test_code_assessment
from workflow_harness import (acceptance_ids, ensure_local_run_dir, evidence_digest,
                              lexical_absolute, plans, read_state, repo_for_area,
                              required_slots, review_depth)


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


def optional_review_context(run_dir: Path) -> dict:
    context = {}
    source_path = run_dir / "official-sources.json"
    if source_path.is_file() and not source_path.is_symlink():
        source_data = json.loads(source_path.read_text(encoding="utf-8"))
        context["official_sources"] = source_data
        context["official_sources_digest"] = hashlib.sha256(source_path.read_bytes()).hexdigest()
    scope_path = run_dir / "scope-register.json"
    if scope_path.is_file() and not scope_path.is_symlink():
        scope_data = json.loads(scope_path.read_text(encoding="utf-8"))
        questions = scope_data.get("questions", []) if isinstance(scope_data, dict) else []
        context["scope_questions"] = [
            {key: item.get(key) for key in
             ("id", "kind", "status", "question", "paths", "impact", "blocks_requested_work")}
            for item in questions if isinstance(item, dict)
        ]
        context["scope_digest"] = hashlib.sha256(scope_path.read_bytes()).hexdigest()
    return context


def acceptance_criteria(run_dir: Path, area: str) -> list[dict[str, str]]:
    content = (run_dir / f"{area}-design.md").read_bytes().decode("utf-8")
    return acceptance_criteria_from_text(content, area)


def acceptance_criteria_from_text(content: str, area: str) -> list[dict[str, str]]:
    section = re.search(r"(?ms)^##\s+(?:수용 기준|Acceptance Criteria)\s*$\n(.*?)(?=^##\s+|\Z)",
                        content)
    if not section:
        raise ValueError(f"design needs a ## 수용 기준 section: {area}")
    matcher = re.compile(r"^\s*(?:[-*]\s*)?((?:AC-[\w.-]+)|(?:R\d+))\s*[.:)\-]\s*(.*)$")
    criteria = []
    for line in section.group(1).splitlines():
        if not line.strip():
            continue
        match = matcher.match(line)
        if match:
            criteria.append({"id": match.group(1), "text": match.group(2).strip()})
    return criteria


def design_delta(run_dir: Path, state: dict, area: str, criteria: list[dict[str, str]]) -> dict:
    design_bytes = (run_dir / f"{area}-design.md").read_bytes()
    design_text = design_bytes.decode("utf-8")
    current_digest = hashlib.sha256(design_bytes).hexdigest()
    history_dir = run_dir / ".review-history"
    if history_dir.is_symlink():
        raise ValueError("review history directory must not be a symbolic link")
    history_dir.mkdir(exist_ok=True)
    current_path = history_dir / f"{area}-{current_digest}.json"
    if current_path.is_symlink():
        raise ValueError("review history snapshot must not be a symbolic link")
    current_snapshot = None
    if current_path.exists():
        current_snapshot = validate_snapshot(current_path, current_digest, area)
        if current_snapshot is not None:
            design_text = current_snapshot["design_text"]
    if not current_path.exists() or current_snapshot is None:
        temporary = history_dir / f".{uuid.uuid4().hex}.tmp"
        temporary.write_text(json.dumps({"schema_version": 2, "digest": current_digest,
                                        "design_text": design_text},
                                        ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, current_path)

    prior_events = [event for event in state.get("events", [])
                    if event.get("type") == "review" and event.get("area") == area
                    and event.get("digest") != current_digest]
    baseline = None
    if prior_events:
        prior_digest = prior_events[-1].get("digest")
        if isinstance(prior_digest, str) and re.fullmatch(r"[0-9a-f]{64}", prior_digest):
            prior_path = history_dir / f"{area}-{prior_digest}.json"
            if prior_path.is_file() and not prior_path.is_symlink():
                baseline = validate_snapshot(prior_path, prior_digest, area)

    current = {item["id"]: item["text"] for item in criteria}
    previous = ({item["id"]: item["text"] for item in baseline["criteria"]}
                if baseline else current)
    def preview(value: str) -> dict[str, object]:
        return {"text": value[:240], "truncated": len(value) > 240,
                "sha256": hashlib.sha256(value.encode("utf-8")).hexdigest()}

    changes = []
    for criterion_id in sorted(set(previous) | set(current)):
        if criterion_id not in previous:
            changes.append({"id": criterion_id, "change": "added",
                            "current": preview(current[criterion_id])})
        elif criterion_id not in current:
            changes.append({"id": criterion_id, "change": "removed",
                            "previous": preview(previous[criterion_id])})
        elif previous[criterion_id] != current[criterion_id]:
            changes.append({"id": criterion_id, "change": "updated",
                            "previous": preview(previous[criterion_id]),
                            "current": preview(current[criterion_id])})
    previous_findings = [
        {"id": f"slot-{event.get('slot')}", "summary": str(event.get("finding", ""))[:240]}
        for event in prior_events if event.get("result") == "changes-required"
    ]
    return {"baseline_status": "available" if baseline else "unavailable",
            "baseline_digest": baseline.get("digest") if baseline else None,
            "current_digest": current_digest,
            "changed_criteria": changes,
            "current_criteria": [{"id": item["id"], "text": item["text"][:240],
                                  "truncated": len(item["text"]) > 240,
                                  "sha256": hashlib.sha256(item["text"].encode("utf-8")).hexdigest()}
                                 for item in criteria],
            "previous_review_findings": previous_findings}


def validate_snapshot(path: Path, expected_digest: str, area: str) -> dict | None:
    candidate = json.loads(path.read_text(encoding="utf-8"))
    design_text = candidate.get("design_text") if isinstance(candidate, dict) else None
    if isinstance(candidate, dict) and candidate.get("digest") == expected_digest \
            and "design_text" not in candidate and isinstance(candidate.get("criteria"), list):
        return None
    if (not isinstance(design_text, str) or candidate.get("schema_version") != 2
            or candidate.get("digest") != expected_digest
            or hashlib.sha256(design_text.encode("utf-8")).hexdigest() != expected_digest):
        raise ValueError("review history snapshot changed after recording")
    return {"digest": expected_digest,
            "design_text": design_text,
            "criteria": acceptance_criteria_from_text(design_text, area)}


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
    criteria_details = {name: acceptance_criteria(run_dir, name) for name in area_names}
    deltas = {name: design_delta(run_dir, state, name, criteria_details[name])
              for name in area_names}
    path_areas = (["frontend", "backend", "integration"] if area == "integration"
                  else [area])
    changed = []
    for item in path_areas:
        item_base = base or state.get("base_commits", {}).get(item)
        paths = changed_paths(repo_for_area(state, item), item_base)
        changed.extend(f"{item}:{path}" for path in paths)
    result = {
        "stage": stage, "area": area, "slot": slot,
        "focus": focus_for(stage, area, slot, review_depth(state)),
        "question": focus_question(stage, area, slot, review_depth(state)),
        "source": source, "source_version": source_version,
        "approval_reference": approval_reference,
        "design_files": [str(run_dir / f"{name}-design.md") for name in area_names],
        "plan_files": [str(run_dir / "verification-plan.json"), str(run_dir / "qa-plan.json")],
        "acceptance_ids": criteria,
        "design_delta": deltas,
        "changed_paths": changed,
        "open_findings": open_findings(run_dir, state, area),
        **optional_review_context(run_dir),
        "evidence_dir": str(run_dir),
        "policies": ["read-only review", f"{len(required_slots(state))} distinct agent ID(s)",
                     "expand context when dependencies or ownership are uncertain",
                     "never convert failed or unexecuted evidence to PASS"],
        "spawn": {"preferred": {"fork_turns": "none", "context_mode": "isolated"},
                  "fallback": {"fork_turns": "all", "context_mode": "full-fallback",
                               "rule": "If isolation is unsupported, record why and provide full context; keep distinct agents."}},
        "selection": choose_model(stage, slot, supported, default_model, default_effort),
    }
    assessment_slot = 3 if review_depth(state) == "full" else 1
    if (stage == "code-review" and requires_test_code_assessment(state, run_dir)
            and slot == assessment_slot):
        verification, _ = plans(run_dir, state)
        result["test_code_assessment_schema"] = {
            "required_fields": ["outcome", "rationale", "acceptance_criteria",
                                "required_test_ids", "change_paths", "reviewed_test_paths",
                                "test_path_evidence", "omission_category"],
            "outcome": ["verified", "not-needed"],
            "rationale": "non-empty string",
            "acceptance_criteria": sorted(criteria[area]),
            "required_test_ids": sorted(item["id"] for item in verification[area]
                                         if item["kind"] == "test" and item["required"]),
            "change_paths": [path for path in changed if path.startswith(area + ":")],
            "verified": {"reviewed_test_paths": "non-empty unique subset of change_paths",
                         "test_path_evidence": "one {path, location, test_behavior} per reviewed path; explain repo-specific or inline test locations",
                         "omission_category": None},
            "not-needed": {"reviewed_test_paths": [],
                           "test_path_evidence": [],
                           "omission_category": ["documentation-only", "formatting-only",
                                                 "mechanical-rename", "low-risk-simple-change"]},
        }
    return result


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
