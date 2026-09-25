#!/usr/bin/env python3
"""Validate local reviewer results and render slot and aggregate Markdown."""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path

from orchestrate import focus_for
from workflow_harness import (agent_artifact, evidence_digest, lexical_absolute,
                              read_state, required_slots, review_depth)


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
    if stage not in RESULTS or area not in {"frontend", "backend", "integration"} \
            or type(slot) is not int or slot not in (1, 2, 3):
        raise ValueError("invalid result stage, area, or slot")
    if stage == "code-review" and area == "integration":
        raise ValueError("integration code review is not a workflow stage")
    if slot not in required_slots(state) or data.get("focus") != focus_for(stage, area, slot, review_depth(state)) \
            or data.get("result") not in RESULTS[stage]:
        raise ValueError("result focus or outcome does not match slot")
    if not isinstance(data.get("agent_id"), str) or not data["agent_id"].strip():
        raise ValueError("result needs an agent ID")
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
    return data, relative, digest


def markdown(data: dict) -> str:
    lines = [f"# {data['stage']} {data['area']} slot {data['slot']}", "",
             f"- agent_id: `{data['agent_id']}`", f"- focus: `{data['focus']}`",
             f"- result: **{data['result']}**", "", "## Findings", ""]
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
