#!/usr/bin/env python3
"""Build a scope-aware task graph for the implementation workflow."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


FOCUSES = {
    "design-review": (
        ("requirements", "요구사항과 수용 기준이 설계에 빠짐없이 연결되는가?"),
        ("architecture", "구조, 계약, 호환성과 구현 순서가 성립하는가?"),
        ("failure", "오류·보안·운영 경로를 검증할 수 있는가?"),
    ),
    "contract-review": (
        ("fields", "요청·응답 필드와 데이터 형식이 일치하는가?"),
        ("auth-errors", "인증·권한과 오류 의미가 양쪽에서 같은가?"),
        ("compatibility", "호환성과 배포 순서가 안전한가?"),
    ),
    "code-review": (
        ("design-regression", "구현이 설계·수용 기준과 맞고 회귀가 없는가?"),
        ("correctness-security", "로직·데이터·권한·보안 결함이 있는가?"),
        ("tests-maintainability", "테스트 빈틈과 유지보수 위험이 있는가?"),
    ),
    "qa": (
        ("happy-path", "핵심 사용자 흐름의 실제 결과가 기대와 같은가?"),
        ("edge-failure", "경계값·오류·권한 흐름이 올바른가?"),
        ("regression-operations", "회귀와 운영 환경에서 실패하는 흐름이 있는가?"),
    ),
    "integration-qa": (
        ("contract", "프론트엔드·백엔드 API 계약이 실제로 맞는가?"),
        ("end-to-end", "사용자 흐름이 양쪽을 지나 끝까지 동작하는가?"),
        ("integration-regression", "통합 오류와 복구·회귀가 없는가?"),
    ),
}

ROLE_MODELS = {
    "design-review": (("gpt-6-luna", "medium"),) * 3,
    "code-review": (("gpt-6-luna", "medium"),) * 3,
    "qa": (("gpt-6-luna", "medium"),) * 3,
}


def model_for(stage: str, slot: int) -> dict[str, str]:
    model, effort = ROLE_MODELS[stage][slot - 1]
    return {"recommended_model": model, "recommended_effort": effort}


def choose_model(stage: str, slot: int, supported: dict[str, list[str]],
                 default_model: str, default_effort: str) -> dict[str, str]:
    preferred = model_for(stage, slot)
    model = preferred["recommended_model"]
    effort = preferred["recommended_effort"]
    available = supported.get(model, [])
    if effort in available:
        return {"model": model, "effort": effort, "reason": "recommended"}
    ranks = ("low", "medium", "high", "xhigh", "max", "ultra")
    if effort in ranks:
        lower = [candidate for candidate in ranks[:ranks.index(effort)] if candidate in available]
        if lower:
            return {"model": model, "effort": lower[-1], "reason": "recommended effort unavailable"}
    if default_effort not in supported.get(default_model, []):
        raise ValueError("no supported model/effort fallback")
    return {"model": default_model, "effort": default_effort,
            "reason": "recommended model or effort unavailable"}


def focus_for(stage: str, area: str, slot: int, depth: str = "full") -> str:
    if depth == "light":
        return "comprehensive"
    profile = "contract-review" if stage == "design-review" and area == "integration" else (
        "integration-qa" if stage == "qa" and area == "integration" else stage
    )
    return FOCUSES[profile][slot - 1][0]


def focus_question(stage: str, area: str, slot: int, depth: str = "full") -> str:
    if depth == "light":
        return {
            "design-review": "요구사항·구조·실패 경로가 모두 검증 가능한가?",
            "code-review": "설계 일치·정확성·보안·테스트 누락이 없는가?",
            "qa": "정상·경계·권한·회귀·운영 시나리오의 실제 증거가 있는가?",
        }[stage]
    profile = "contract-review" if stage == "design-review" and area == "integration" else (
        "integration-qa" if stage == "qa" and area == "integration" else stage
    )
    return FOCUSES[profile][slot - 1][1]


def build_plan(scope: str, review_mode: str = "immediate",
               review_depth: str = "full") -> list[dict[str, object]]:
    if scope not in {"frontend", "backend", "both"}:
        raise ValueError(f"unsupported scope: {scope}")
    if review_mode not in {"immediate", "user-review"}:
        raise ValueError(f"unsupported review mode: {review_mode}")
    if review_depth not in {"light", "full"} or (scope == "both" and review_depth == "light"):
        raise ValueError("light review is only available for one area")
    slots = (1,) if review_depth == "light" else (1, 2, 3)

    areas = ["frontend", "backend"] if scope == "both" else [scope]
    tasks: list[dict[str, object]] = [
        {"id": "intake", "role": "coordinator", "depends_on": []}
    ]
    for area in areas:
        tasks.append({"id": f"design-{area}", "role": f"{area}-designer", "depends_on": ["intake"]})
        design_dependency = f"design-{area}"
        if review_mode == "user-review":
            tasks.append({"id": f"present-design-{area}", "role": "coordinator",
                          "depends_on": [design_dependency]})
            design_dependency = f"present-design-{area}"
        for slot in slots:
            tasks.append({"id": f"review-design-{area}-{slot}",
                          "role": f"{area}-design-reviewer-{slot}",
                          "focus": focus_for("design-review", area, slot, review_depth),
                          "question": focus_question("design-review", area, slot, review_depth),
                          **model_for("design-review", slot),
                          "depends_on": [design_dependency]})
    design_reviews = [f"review-design-{area}-{slot}" for area in areas for slot in slots]
    if scope == "both":
        for slot in slots:
            tasks.append({"id": f"review-contract-{slot}", "role": f"contract-reviewer-{slot}",
                          "focus": focus_for("design-review", "integration", slot, review_depth),
                          "question": focus_question("design-review", "integration", slot, review_depth),
                          **model_for("design-review", slot),
                          "depends_on": design_reviews})
        design_reviews = [f"review-contract-{slot}" for slot in slots]
    if review_mode == "user-review":
        tasks.append({"id": "accept-design", "role": "user", "depends_on": design_reviews})
        design_reviews = ["accept-design"]
    tasks.append({"id": "approval-gate", "role": "coordinator", "depends_on": design_reviews})
    for area in areas:
        tasks.append({"id": f"implement-{area}", "role": f"{area}-implementer", "depends_on": ["approval-gate"]})
        tasks.append({"id": f"test-{area}", "role": f"{area}-implementer", "depends_on": [f"implement-{area}"]})
        code_reviews = []
        for slot in slots:
            task_id = f"review-code-{area}-{slot}"
            tasks.append({"id": task_id, "role": f"{area}-code-reviewer-{slot}",
                          "focus": focus_for("code-review", area, slot, review_depth),
                          "question": focus_question("code-review", area, slot, review_depth),
                          **model_for("code-review", slot),
                          "depends_on": [f"test-{area}"]})
            code_reviews.append(task_id)
        for slot in slots:
            tasks.append({"id": f"qa-{area}-{slot}", "role": f"{area}-qa-{slot}",
                          "focus": focus_for("qa", area, slot, review_depth),
                          "question": focus_question("qa", area, slot, review_depth),
                          **model_for("qa", slot),
                          "depends_on": code_reviews})
    qa_tasks = [f"qa-{area}-{slot}" for area in areas for slot in slots]
    if scope == "both":
        for slot in slots:
            tasks.append({"id": f"qa-integration-{slot}", "role": f"integration-qa-{slot}",
                          "focus": focus_for("qa", "integration", slot, review_depth),
                          "question": focus_question("qa", "integration", slot, review_depth),
                          **model_for("qa", slot),
                          "depends_on": qa_tasks})
        qa_tasks = [f"qa-integration-{slot}" for slot in slots]
    tasks.append({"id": "final-report", "role": "coordinator", "depends_on": qa_tasks})
    return tasks


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", choices=("frontend", "backend", "both"))
    parser.add_argument("--review-mode", choices=("immediate", "user-review"))
    parser.add_argument("--review-depth", choices=("light", "full"))
    parser.add_argument("--run-dir", type=Path)
    args = parser.parse_args()
    if args.run_dir:
        result = subprocess.run(
            [sys.executable, str(Path(__file__).with_name("workflow_harness.py")),
             "status", "--run-dir", str(args.run_dir)],
            text=True, capture_output=True,
        )
        if result.returncode:
            parser.error(result.stderr.strip() or "run status unavailable")
        status = json.loads(result.stdout)
        state = json.loads((args.run_dir / "state.json").read_text(encoding="utf-8"))
        scope = state["scope"]
        mode = state["review_mode"]
        depth = state.get("review_depth", "full")
        if (args.scope and args.scope != scope) or (args.review_mode and args.review_mode != mode) \
                or (args.review_depth and args.review_depth != depth):
            parser.error("run state conflicts with requested plan options")
        print(json.dumps({"status": status, "plan": build_plan(scope, mode, depth)},
                         ensure_ascii=False, indent=2))
        return
    if not args.scope or not args.review_mode:
        parser.error("--scope and --review-mode are required without --run-dir")
    print(json.dumps(build_plan(args.scope, args.review_mode, args.review_depth or "full"),
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
