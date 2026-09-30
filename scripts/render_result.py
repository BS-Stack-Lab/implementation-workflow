#!/usr/bin/env python3
"""Validate local reviewer results and render slot and aggregate Markdown."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import uuid
from pathlib import Path

from orchestrate import focus_for
from workflow_harness import (agent_artifact, evidence_digest, lexical_absolute,
                              acceptance_ids, changed_code_paths, plans, read_state,
                              required_slots, review_depth, run_policy_data)


RESULTS = {
    "design-review": {"clear", "changes-required"},
    "code-review": {"clear", "findings"},
    "qa": {"pass", "fail", "not-run"},
}


def validate(run_dir: Path, raw_path: str) -> tuple[dict, str, str]:
    state = read_state(run_dir)
    relative, digest = evidence_digest(run_dir, raw_path)
    data = json.loads((run_dir / relative).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("invalid result schema")
    stage, area, slot = data.get("stage"), data.get("area"), data.get("slot")
    if not isinstance(stage, str) or stage not in RESULTS \
            or not isinstance(area, str) or area not in {"frontend", "backend", "integration"} \
            or type(slot) is not int or slot not in (1, 2, 3):
        raise ValueError("invalid result stage, area, or slot")
    if stage == "code-review" and area == "integration":
        raise ValueError("integration code review is not a workflow stage")
    result = data.get("result")
    if not isinstance(result, str) or slot not in required_slots(state) \
            or data.get("focus") != focus_for(stage, area, slot, review_depth(state)) \
            or result not in RESULTS[stage]:
        raise ValueError("result focus or outcome does not match slot")
    if not isinstance(data.get("agent_id"), str) or not data["agent_id"].strip():
        raise ValueError("result needs an agent ID")
    if state.get("policy_version", 1) >= 3 and stage in {"design-review", "code-review"} \
            and data.get("risk_level") not in {"low", "medium", "high", "unknown"}:
        raise ValueError(f"policy v3 {stage} needs an explicit risk_level")
    findings = data.get("findings")
    references = data.get("evidence")
    if not isinstance(findings, list) or not isinstance(references, list):
        raise ValueError("result needs findings and evidence arrays")
    ids = set()
    for finding in findings:
        fields = ("id", "location", "observation", "impact", "correction", "reproduction")
        if not isinstance(finding, dict) or any(not isinstance(finding.get(key), str)
                                              or not finding[key].strip() for key in fields):
            raise ValueError("incomplete finding")
        if finding["id"] in ids:
            raise ValueError("duplicate finding ID")
        ids.add(finding["id"])
        if "evidence" in finding:
            evidence_digest(run_dir, str(run_dir / finding["evidence"]))
    for reference in references:
        if not isinstance(reference, str):
            raise ValueError("invalid evidence reference")
        evidence_digest(run_dir, str(run_dir / reference))
    if data["result"] in {"clear", "pass"} and findings:
        raise ValueError("clear/pass result conflicts with findings")
    if data["result"] in {"changes-required", "findings", "fail"} and not findings:
        raise ValueError("negative result needs a finding")
    assessment_required = requires_test_code_assessment(state, run_dir)
    if (assessment_required
            and stage == "code-review"
            and slot == (3 if review_depth(state) == "full" else 1)):
        validate_test_code_assessment(run_dir, state, data)
    return data, relative, digest


def requires_test_code_assessment(state: dict, run_dir: Path | None = None) -> bool:
    if state.get("version", 0) < 8:
        return False
    policy_version = state.get("policy_version")
    if type(policy_version) is not int:
        raise ValueError("v8 run needs an integer policy_version")
    run_id = state.get("run_id")
    if not isinstance(run_id, str):
        raise ValueError("v8 run needs a string run_id")
    match = re.match(r"^v8p([0-9]+)-", run_id)
    versioned_policy = int(match.group(1)) if match else None
    if (versioned_policy is not None and versioned_policy != policy_version) \
            or (policy_version >= 2 and versioned_policy is None):
        raise ValueError("v8 run identity does not match its initialized policy version")
    if run_dir is not None:
        marker_path = run_dir / "run-policy.json"
        if marker_path.is_symlink():
            raise ValueError("run policy marker cannot be a symbolic link")
        if marker_path.exists():
            if not marker_path.is_file():
                raise ValueError("run policy marker must be a regular file")
            try:
                marker = json.loads(marker_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError) as error:
                raise ValueError("invalid run policy marker") from error
            marker_version = marker.get("policy_version") if isinstance(marker, dict) else None
            if not isinstance(state.get("run_id"), str) or type(marker_version) is not int \
                    or marker != run_policy_data(state["run_id"], marker_version) \
                    or marker_version != policy_version:
                raise ValueError("run policy marker does not match initialized run state")
        elif policy_version >= 2:
            raise ValueError("new v8 run is missing its initialization policy marker")
    return policy_version >= 2


def validate_test_code_assessment(run_dir: Path, state: dict, data: dict) -> None:
    assessment = data.get("test_code_assessment")
    if not isinstance(assessment, dict):
        raise ValueError("required code-review slot needs test_code_assessment")
    allowed = {"outcome", "rationale", "acceptance_criteria", "required_test_ids",
               "change_paths", "reviewed_test_paths", "test_path_evidence", "omission_category"}
    if set(assessment) != allowed:
        raise ValueError("test_code_assessment fields do not match the required schema")
    outcome = assessment.get("outcome")
    rationale = assessment.get("rationale")
    if not isinstance(outcome, str) or outcome not in {"verified", "not-needed"} \
            or not isinstance(rationale, str) \
            or not rationale.strip():
        raise ValueError("test_code_assessment needs a valid outcome and non-empty rationale")
    def string_set(field: str) -> set[str]:
        values = assessment.get(field)
        if not isinstance(values, list) or not values or any(
                not isinstance(item, str) or not item.strip() for item in values):
            raise ValueError(f"test_code_assessment {field} must be a non-empty string array")
        if len(values) != len(set(values)):
            raise ValueError(f"test_code_assessment {field} must not contain duplicates")
        return set(values)

    criteria = string_set("acceptance_criteria")
    required_tests = string_set("required_test_ids")
    changes = string_set("change_paths")
    checks, _ = plans(run_dir, state)
    expected_tests = {item["id"] for item in checks[data["area"]]
                      if item["kind"] == "test" and item["required"]}
    if required_tests != expected_tests:
        raise ValueError("test_code_assessment required_test_ids must cover every required test")
    if criteria != acceptance_ids(run_dir, data["area"]):
        raise ValueError("test_code_assessment acceptance_criteria must cover every criterion")
    actual_changes = {path for path in changed_code_paths(state) if path.startswith(data["area"] + ":")}
    if not changes or changes != actual_changes:
        raise ValueError("test_code_assessment change_paths must list every changed path in this area")
    reviewed = assessment.get("reviewed_test_paths")
    if outcome == "verified":
        if not isinstance(reviewed, list) or not reviewed or any(
                not isinstance(path, str) or not path.strip() for path in reviewed):
            raise ValueError("verified test_code_assessment needs reviewed_test_paths")
        if len(reviewed) != len(set(reviewed)) or any(path not in changes for path in reviewed):
            raise ValueError("reviewed_test_paths must be unique changed paths")
        path_evidence = assessment.get("test_path_evidence")
        if not isinstance(path_evidence, list) or not path_evidence:
            raise ValueError("verified assessment needs test_path_evidence")
        evidenced_paths = set()
        for item in path_evidence:
            if not isinstance(item, dict) or set(item) != {"path", "location", "test_behavior"} \
                    or any(not isinstance(item.get(key), str) or not item[key].strip()
                           for key in ("path", "location", "test_behavior")):
                raise ValueError("test_path_evidence entries need path, location, and test_behavior")
            if item["path"] not in reviewed:
                raise ValueError("test_path_evidence path must be listed in reviewed_test_paths")
            evidenced_paths.add(item["path"])
        if evidenced_paths != set(reviewed):
            raise ValueError("every reviewed_test_path needs test_path_evidence")
        if assessment.get("omission_category") is not None:
            raise ValueError("verified test_code_assessment cannot have an omission_category")
    else:
        omission_category = assessment.get("omission_category")
        if not isinstance(reviewed, list) or reviewed != [] \
                or assessment.get("test_path_evidence") != [] \
                or not isinstance(omission_category, str) or omission_category not in {
                    "documentation-only", "formatting-only", "mechanical-rename",
                    "low-risk-simple-change"}:
            raise ValueError("not-needed assessment needs an allowed omission_category and no test paths")


def markdown(data: dict) -> str:
    lines = [f"# {data['stage']} {data['area']} slot {data['slot']}", "",
             f"- agent_id: `{data['agent_id']}`", f"- focus: `{data['focus']}`",
             f"- result: **{data['result']}**"]
    if "risk_level" in data:
        lines.append(f"- risk_level: **{data['risk_level']}**")
    lines += ["", "## Findings", ""]
    if not data["findings"]:
        lines.append("- None")
    for finding in data["findings"]:
        lines += [f"### {finding['id']}", "", f"- Location: {finding['location']}",
                  f"- Observation: {finding['observation']}", f"- Impact: {finding['impact']}",
                  f"- Correction: {finding['correction']}",
                  f"- Reproduction: {finding['reproduction']}"]
        if finding.get("evidence"):
            lines.append(f"- Evidence: `{finding['evidence']}`")
        lines.append("")
    lines += ["## Evidence", ""]
    lines += [f"- `{reference}`" for reference in data["evidence"]] or ["- None"]
    if "test_code_assessment" in data:
        assessment = data["test_code_assessment"]
        lines += ["", "## Test code assessment", "",
                  f"- Outcome: **{assessment['outcome']}**",
                  f"- Rationale: {assessment['rationale']}",
                  f"- Acceptance criteria: {', '.join(assessment['acceptance_criteria'])}",
                  f"- Required test IDs: {', '.join(assessment['required_test_ids'])}",
                  f"- Changed paths: {', '.join(assessment['change_paths'])}",
                  f"- Reviewed test paths: {', '.join(assessment['reviewed_test_paths']) or 'None'}"]
        for evidence in assessment.get("test_path_evidence", []):
            lines.append(f"- Test evidence: `{evidence['path']}` — {evidence['location']}: "
                         f"{evidence['test_behavior']}")
        if assessment.get("omission_category"):
            lines.append(f"- Omission category: `{assessment['omission_category']}`")
    return "\n".join(lines) + "\n"


def aggregate(items: list[dict], slots: tuple[int, ...] = (1, 2, 3)) -> str:
    if len(items) != len(slots) or {item["slot"] for item in items} != set(slots) \
            or len({item["agent_id"] for item in items}) != len(slots) \
            or len({(item["stage"], item["area"]) for item in items}) != 1:
        raise ValueError("aggregate needs distinct agents and every required slot for one stage/area")
    ids = [finding["id"] for item in items for finding in item["findings"]]
    if len(ids) != len(set(ids)):
        raise ValueError("finding IDs must be unique across slots")
    items = sorted(items, key=lambda item: item["slot"])
    stage, area = items[0]["stage"], items[0]["area"]
    lines = [f"# {stage} {area} aggregate", ""]
    for item in items:
        lines += [f"## Slot {item['slot']}: {item['focus']}", "",
                  f"- Agent: `{item['agent_id']}`", f"- Result: **{item['result']}**", ""]
        if "test_code_assessment" in item:
            assessment = item["test_code_assessment"]
            lines += [f"- Test code assessment: **{assessment['outcome']}** — {assessment['rationale']}",
                      f"- Assessed criteria: {', '.join(assessment['acceptance_criteria'])}",
                      f"- Required test IDs: {', '.join(assessment['required_test_ids'])}", ""]
        for finding in item["findings"]:
            lines.append(f"- **{finding['id']}** {finding['observation']} ({finding['location']})")
        if not item["findings"]:
            lines.append("- No findings")
        lines.append("")
    lines += ["## Conflicting opinions", "",
              "Keep each slot's original conclusion above; resolve differences in the final report.", ""]
    return "\n".join(lines)


def write_current(run_dir: Path, name: str, content: str) -> Path:
    target = run_dir / name
    if target.exists():
        if target.is_symlink() or not target.is_file():
            raise ValueError("current artifact is not a regular file")
        archive = run_dir / f"{target.stem}-attempt-{uuid.uuid4().hex}{target.suffix}"
        archive.write_bytes(target.read_bytes())
    temporary = run_dir / f".{name}.{uuid.uuid4().hex}.tmp"
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, target)
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--input-json", action="append", required=True)
    parser.add_argument("--aggregate", action="store_true")
    args = parser.parse_args()
    try:
        run_dir = lexical_absolute(args.run_dir)
        state = read_state(run_dir)
        items = [validate(run_dir, path)[0] for path in args.input_json]
        if args.aggregate:
            name = ("integration-contract-review.md" if items[0]["stage"] == "design-review"
                    and items[0]["area"] == "integration" else
                    f"{items[0]['area']}-{items[0]['stage']}.md")
            print(write_current(run_dir, name, aggregate(items, required_slots(state))))
        else:
            if len(items) != 1:
                raise ValueError("one JSON input is required without --aggregate")
            item = items[0]
            print(write_current(run_dir, agent_artifact(item["stage"], item["area"],
                                                    item["slot"]), markdown(item)))
        return 0
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(f"render_result: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
