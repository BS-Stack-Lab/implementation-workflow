#!/usr/bin/env python3
"""Build a scope-aware task graph for the implementation workflow."""

from __future__ import annotations

import argparse
import json


def build_plan(scope: str) -> list[dict[str, object]]:
    if scope not in {"frontend", "backend", "both"}:
        raise ValueError(f"unsupported scope: {scope}")

    areas = ["frontend", "backend"] if scope == "both" else [scope]
    tasks: list[dict[str, object]] = [
        {"id": "intake", "role": "coordinator", "depends_on": []}
    ]
    for area in areas:
        tasks.append({"id": f"design-{area}", "role": f"{area}-designer", "depends_on": ["intake"]})
        tasks.append({"id": f"review-design-{area}", "role": f"{area}-design-reviewer", "depends_on": [f"design-{area}"]})
    design_reviews = [f"review-design-{area}" for area in areas]
    if scope == "both":
        tasks.append({"id": "review-contract", "role": "contract-reviewer", "depends_on": design_reviews})
        design_reviews = ["review-contract"]
    tasks.append({"id": "approval-gate", "role": "coordinator", "depends_on": design_reviews})
    for area in areas:
        tasks.append({"id": f"implement-{area}", "role": f"{area}-implementer", "depends_on": ["approval-gate"]})
        tasks.append({"id": f"test-{area}", "role": f"{area}-implementer", "depends_on": [f"implement-{area}"]})
        tasks.append({"id": f"review-code-{area}", "role": f"{area}-code-reviewer", "depends_on": [f"test-{area}"]})
        tasks.append({"id": f"qa-{area}", "role": f"{area}-qa", "depends_on": [f"review-code-{area}"]})
    qa_tasks = [f"qa-{area}" for area in areas]
    if scope == "both":
        tasks.append({"id": "qa-integration", "role": "integration-qa", "depends_on": qa_tasks})
        qa_tasks = ["qa-integration"]
    tasks.append({"id": "final-report", "role": "coordinator", "depends_on": qa_tasks})
    return tasks


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", choices=("frontend", "backend", "both"), required=True)
    args = parser.parse_args()
    print(json.dumps(build_plan(args.scope), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
