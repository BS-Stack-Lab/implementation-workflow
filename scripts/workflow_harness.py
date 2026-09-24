#!/usr/bin/env python3
"""Keep implementation evidence local and check design/QA stage gates."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path


ARTIFACTS = {
    "frontend": ("frontend-design.md", "frontend-design-review.md", "frontend-code-review.md", "frontend-qa.md"),
    "backend": ("backend-design.md", "backend-design-review.md", "backend-code-review.md", "backend-qa.md"),
}


def areas_for(scope: str) -> list[str]:
    return ["frontend", "backend"] if scope == "both" else [scope]


def review_areas(scope: str) -> list[str]:
    return areas_for(scope) + (["integration"] if scope == "both" else [])


def within(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def ensure_local_run_dir(run_dir: Path, repo_root: Path) -> None:
    resolved = run_dir.resolve()
    base = (Path.home() / ".codex" / "implementation-workflow-runs").resolve()
    if not within(resolved, base):
        raise ValueError("run directory must be under ~/.codex/implementation-workflow-runs")
    if within(resolved, repo_root.resolve()):
        raise ValueError("run directory must be outside the target Git repository")
    if any((parent / ".git").exists() for parent in (resolved, *resolved.parents)):
        raise ValueError("run directory must not be inside any Git repository")


def default_run_dir(repo_root: Path) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return Path.home() / ".codex" / "implementation-workflow-runs" / f"{repo_root.name}-{timestamp}-{uuid.uuid4().hex[:6]}"


def read_state(run_dir: Path) -> dict:
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    ensure_local_run_dir(run_dir, Path(state["repo_root"]))
    return state


def write_state(run_dir: Path, state: dict) -> None:
    target = run_dir / "state.json"
    temporary = target.with_name(f".state-{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, target)


def design_digest(run_dir: Path, area: str) -> str:
    names = [f"{area}-design.md"] if area != "integration" else ["frontend-design.md", "backend-design.md"]
    digest = hashlib.sha256()
    for name in names:
        require_artifact(run_dir, name)
        data = (run_dir / name).read_bytes()
        digest.update(name.encode("utf-8") + b"\0" + data + b"\0")
    return digest.hexdigest()


def require_artifact(run_dir: Path, name: str) -> None:
    path = run_dir / name
    if path.is_symlink() or not within(path.resolve(), run_dir.resolve()):
        raise ValueError(f"artifact must be a local file within the run directory: {name}")
    if not path.is_file() or not path.read_bytes().strip():
        raise ValueError(f"missing or empty artifact: {name}")


def check_design(run_dir: Path, state: dict) -> None:
    for area in areas_for(state["scope"]):
        require_artifact(run_dir, f"{area}-design.md")
        require_artifact(run_dir, f"{area}-design-review.md")
    if state["scope"] == "both":
        require_artifact(run_dir, "integration-contract-review.md")

    for area in review_areas(state["scope"]):
        events = [event for event in state["events"] if event["area"] == area]
        reviews = [(index, event) for index, event in enumerate(events) if event["type"] == "review"]
        if not reviews:
            raise ValueError(f"missing design review event: {area}")
        for index, review in reviews:
            next_review = next((later for later in range(index + 1, len(events)) if events[later]["type"] == "review"), len(events))
            if review["result"] == "changes-required" and not any(
                event["type"] == "approval" for event in events[index + 1:next_review]
            ):
                raise ValueError(f"design changes await user approval: {area}")
        _, latest = reviews[-1]
        if latest["result"] != "clear" or latest["digest"] != design_digest(run_dir, area):
            raise ValueError(f"current design needs a clear review: {area}")


def check_final(run_dir: Path, state: dict) -> None:
    check_design(run_dir, state)
    for area in areas_for(state["scope"]):
        for name in ARTIFACTS[area][2:]:
            require_artifact(run_dir, name)
    if state["scope"] == "both":
        require_artifact(run_dir, "integration-qa.md")
    require_artifact(run_dir, "final-report.md")

    for area in review_areas(state["scope"]):
        checks = [event for event in state["events"] if event["type"] == "check" and event["area"] == area]
        if not checks:
            raise ValueError(f"missing verification record: {area}")
        latest = {event["name"]: event for event in checks}
        failed = [name for name, event in latest.items() if event["status"] == "fail"]
        if failed:
            raise ValueError(f"unresolved failed checks for {area}: {', '.join(failed)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    initialize = commands.add_parser("init")
    initialize.add_argument("--repo", type=Path, required=True)
    initialize.add_argument("--scope", choices=("frontend", "backend", "both"), required=True)
    initialize.add_argument("--run-dir", type=Path)
    for command in ("review", "approve", "check-record", "check"):
        sub = commands.add_parser(command)
        sub.add_argument("--run-dir", type=Path, required=True)
        if command in {"review", "approve", "check-record"}:
            sub.add_argument("--area", choices=("frontend", "backend", "integration"), required=True)
        if command == "review":
            sub.add_argument("--result", choices=("clear", "changes-required"), required=True)
            sub.add_argument("--finding", default="")
        elif command == "approve":
            sub.add_argument("--reference", required=True, help="Reference to the user's actual approval")
        elif command == "check-record":
            sub.add_argument("--name", required=True)
            sub.add_argument("--status", choices=("pass", "fail", "not-run"), required=True)
            sub.add_argument("--evidence", required=True)
        else:
            sub.add_argument("--gate", choices=("design", "final"), required=True)
    args = parser.parse_args()

    try:
        if args.command == "init":
            repo_root = args.repo.resolve()
            if not (repo_root / ".git").exists():
                raise ValueError("--repo must be a Git checkout")
            run_dir = (args.run_dir or default_run_dir(repo_root)).resolve()
            ensure_local_run_dir(run_dir, repo_root)
            run_dir.mkdir(parents=True, exist_ok=False)
            run_dir.chmod(0o700)
            write_state(run_dir, {"version": 1, "repo_root": str(repo_root), "scope": args.scope, "events": []})
            print(run_dir)
            return 0

        run_dir = args.run_dir.resolve()
        state = read_state(run_dir)
        if args.command in {"review", "approve", "check-record"} and args.area not in review_areas(state["scope"]):
            raise ValueError(f"area {args.area} is outside the {state['scope']} scope")
        if args.command == "review":
            if args.result == "changes-required" and not args.finding.strip():
                raise ValueError("--finding is required for changes-required review")
            review_file = f"{args.area}-design-review.md" if args.area != "integration" else "integration-contract-review.md"
            require_artifact(run_dir, review_file)
            state["events"].append({"type": "review", "area": args.area, "result": args.result,
                                    "finding": args.finding, "digest": design_digest(run_dir, args.area)})
            write_state(run_dir, state)
        elif args.command == "approve":
            if not args.reference.strip():
                raise ValueError("approval reference cannot be empty")
            events = [event for event in state["events"] if event["area"] == args.area]
            last_review_index = next((index for index in range(len(events) - 1, -1, -1)
                                      if events[index]["type"] == "review"), None)
            last_review = events[last_review_index] if last_review_index is not None else None
            if not last_review or last_review["result"] != "changes-required" or any(
                event["type"] == "approval" for event in events[last_review_index + 1:]
            ):
                raise ValueError("no unapproved design finding for this area")
            if last_review["digest"] != design_digest(run_dir, args.area):
                raise ValueError("design changed before user approval; restore the reviewed version")
            state["events"].append({"type": "approval", "area": args.area,
                                    "reference": args.reference, "digest": last_review["digest"]})
            write_state(run_dir, state)
        elif args.command == "check-record":
            if not args.name.strip() or not args.evidence.strip():
                raise ValueError("check name and evidence cannot be empty")
            state["events"].append({"type": "check", "area": args.area, "name": args.name,
                                    "status": args.status, "evidence": args.evidence})
            write_state(run_dir, state)
        else:
            (check_design if args.gate == "design" else check_final)(run_dir, state)
            print(f"{args.gate} gate passed")
        return 0
    except (OSError, ValueError, json.JSONDecodeError, KeyError) as error:
        print(f"workflow harness: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
