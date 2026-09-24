#!/usr/bin/env python3
"""Keep implementation evidence local and check design/QA stage gates."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path


ARTIFACTS = {
    "frontend": ("frontend-design.md", "frontend-design-review.md", "frontend-code-review.md", "frontend-qa.md"),
    "backend": ("backend-design.md", "backend-design-review.md", "backend-code-review.md", "backend-qa.md"),
}
REVIEW_SLOTS = (1, 2, 3)


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
    if state.get("version") not in (2, 3):
        raise ValueError("unsupported workflow state version")
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


def artifact_digest(run_dir: Path, name: str) -> str:
    require_artifact(run_dir, name)
    return hashlib.sha256((run_dir / name).read_bytes()).hexdigest()


def agent_artifact(stage: str, area: str, slot: int) -> str:
    if stage == "design-review":
        stem = "integration-contract-review" if area == "integration" else f"{area}-design-review"
    elif stage == "code-review":
        stem = f"{area}-code-review"
    else:
        stem = f"{area}-qa"
    return f"{stem}-{slot}.md"


def git_output(repo_root: Path, *arguments: str) -> bytes:
    return subprocess.run(["git", "-C", str(repo_root), *arguments],
                          check=True, capture_output=True).stdout


def code_digest(repo_root: Path) -> str:
    """Fingerprint HEAD, tracked changes, and nonignored untracked files."""
    digest = hashlib.sha256()
    head = subprocess.run(["git", "-C", str(repo_root), "rev-parse", "--verify", "HEAD"],
                          capture_output=True)
    if head.returncode == 0:
        digest.update(head.stdout)
        digest.update(git_output(repo_root, "diff", "--no-ext-diff", "--binary", "HEAD", "--"))
        names = git_output(repo_root, "ls-files", "--others", "--exclude-standard", "-z")
    else:
        digest.update(b"unborn HEAD\0")
        names = git_output(repo_root, "ls-files", "--cached", "--others", "--exclude-standard", "-z")
    for raw_name in sorted(set(names.split(b"\0"))):
        if not raw_name:
            continue
        path = repo_root / os.fsdecode(raw_name)
        digest.update(raw_name + b"\0")
        if path.is_symlink():
            digest.update(b"link\0" + os.fsencode(os.readlink(path)))
        elif path.is_file():
            with path.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
    return digest.hexdigest()


def check_reviews_v2(run_dir: Path, state: dict) -> None:
    for area in areas_for(state["scope"]):
        require_artifact(run_dir, f"{area}-design.md")
        require_artifact(run_dir, f"{area}-design-review.md")
        digest = design_digest(run_dir, area)
        if state["review_mode"] == "user-review" and not any(
            event["type"] == "present" and event["area"] == area and event["digest"] == digest
            for event in state["events"]
        ):
            raise ValueError(f"current design has not been presented to the user: {area}")
    if state["scope"] == "both":
        require_artifact(run_dir, "integration-contract-review.md")

    for area in review_areas(state["scope"]):
        events = [event for event in state["events"] if event.get("area") == area]
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


def latest_slot_results(state: dict, event_type: str, area: str, digest: str) -> dict[int, dict]:
    latest: dict[int, dict] = {}
    for event in state["events"]:
        if event["type"] == event_type and event["area"] == area and event["digest"] == digest:
            latest[event["slot"]] = event
    if set(latest) != set(REVIEW_SLOTS):
        raise ValueError(f"three {event_type} results are required for {area}")
    if len({event["agent_id"] for event in latest.values()}) != 3:
        raise ValueError(f"three distinct subagents are required for {area} {event_type}")
    return latest


def check_reviews_v3(run_dir: Path, state: dict) -> None:
    for area in areas_for(state["scope"]):
        require_artifact(run_dir, f"{area}-design.md")
        require_artifact(run_dir, f"{area}-design-review.md")
        digest = design_digest(run_dir, area)
        if state["review_mode"] == "user-review" and not any(
            event["type"] == "present" and event["area"] == area and event["digest"] == digest
            for event in state["events"]
        ):
            raise ValueError(f"current design has not been presented to the user: {area}")
    if state["scope"] == "both":
        require_artifact(run_dir, "integration-contract-review.md")

    for area in review_areas(state["scope"]):
        digest = design_digest(run_dir, area)
        latest = latest_slot_results(state, "review", area, digest)
        if any(event["result"] != "clear" for event in latest.values()):
            raise ValueError(f"current design needs three clear reviews: {area}")
        if any(event["type"] == "review" and event["area"] == area
               and event["digest"] == digest and event["result"] == "changes-required"
               for event in state["events"]):
            raise ValueError(f"design with findings must be revised and reviewed again: {area}")
        for slot, event in latest.items():
            name = agent_artifact("design-review", area, slot)
            if artifact_digest(run_dir, name) != event["artifact_digest"]:
                raise ValueError(f"design review artifact changed after recording: {name}")
        for index, event in enumerate(state["events"]):
            if event["type"] != "review" or event["area"] != area or event["result"] != "changes-required":
                continue
            if not any(later["type"] == "approval" and later["area"] == area
                       and later["digest"] == event["digest"] for later in state["events"][index + 1:]):
                raise ValueError(f"design findings await user approval: {area}")


def check_reviews(run_dir: Path, state: dict) -> None:
    (check_reviews_v3 if state["version"] == 3 else check_reviews_v2)(run_dir, state)


def check_design(run_dir: Path, state: dict) -> None:
    check_reviews(run_dir, state)
    if state["review_mode"] == "user-review":
        digests = {area: design_digest(run_dir, area) for area in areas_for(state["scope"])}
        if not any(event["type"] == "accept-design" and event["digests"] == digests
                   for event in state["events"]):
            raise ValueError("current design awaits the user's explicit acceptance")


def check_agent_stage(run_dir: Path, state: dict, stage: str, area: str, digest: str) -> None:
    latest = latest_slot_results(state, stage, area, digest)
    wanted = "clear" if stage == "code-review" else "pass"
    for slot, event in latest.items():
        if event["result"] != wanted:
            raise ValueError(f"{stage} result is not {wanted}: {area} slot {slot}")
        if event["design_digest"] != design_digest(run_dir, area):
            raise ValueError(f"{stage} result predates current design: {area} slot {slot}")
        name = agent_artifact(stage, area, slot)
        if artifact_digest(run_dir, name) != event["artifact_digest"]:
            raise ValueError(f"{stage} artifact changed after recording: {name}")
    for index, event in enumerate(state["events"]):
        if event["type"] != stage or event["area"] != area or event["digest"] != digest \
                or event["design_digest"] != design_digest(run_dir, area):
            continue
        if event["result"] not in {"findings", "fail", "not-run"}:
            continue
        resolved = next((later for later in range(index + 1, len(state["events"]))
                         if state["events"][later]["type"] == "agent-resolution"
                         and state["events"][later]["stage"] == stage
                         and state["events"][later]["area"] == area
                         and state["events"][later]["slot"] == event["slot"]
                         and state["events"][later]["digest"] == digest
                         and state["events"][later]["design_digest"] == event["design_digest"]), None)
        if resolved is None or not any(
            later["type"] == stage and later["area"] == area and later["slot"] == event["slot"]
            and later["digest"] == digest and later["design_digest"] == event["design_digest"]
            and later["result"] == wanted
            for later in state["events"][resolved + 1:]
        ):
            raise ValueError(f"{stage} finding needs resolution and rerun: {area} slot {event['slot']}")


def check_final(run_dir: Path, state: dict) -> None:
    check_design(run_dir, state)
    for area in areas_for(state["scope"]):
        for name in ARTIFACTS[area][2:]:
            require_artifact(run_dir, name)
    if state["scope"] == "both":
        require_artifact(run_dir, "integration-qa.md")
    require_artifact(run_dir, "final-report.md")

    current_code = code_digest(Path(state["repo_root"])) if state["version"] == 3 else None
    if current_code is not None:
        for area in areas_for(state["scope"]):
            check_agent_stage(run_dir, state, "code-review", area, current_code)
            check_agent_stage(run_dir, state, "qa", area, current_code)
        if state["scope"] == "both":
            check_agent_stage(run_dir, state, "qa", "integration", current_code)

    for area in review_areas(state["scope"]):
        checks = [event for event in state["events"] if event["type"] == "check" and event["area"] == area]
        if not checks:
            raise ValueError(f"missing verification record: {area}")
        latest = {event["name"]: event for event in checks}
        failed = [name for name, event in latest.items() if event["status"] == "fail"]
        if failed:
            raise ValueError(f"unresolved failed checks for {area}: {', '.join(failed)}")
        if current_code is not None and any(event["digest"] != current_code for event in latest.values()):
            raise ValueError(f"verification record predates current code: {area}")
        if current_code is not None and any(event["design_digest"] != design_digest(run_dir, area)
                                            for event in latest.values()):
            raise ValueError(f"verification record predates current design: {area}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    initialize = commands.add_parser("init")
    initialize.add_argument("--repo", type=Path, required=True)
    initialize.add_argument("--scope", choices=("frontend", "backend", "both"), required=True)
    initialize.add_argument("--review-mode", choices=("immediate", "user-review"), required=True)
    initialize.add_argument("--mode-reference", required=True,
                            help="Reference to the user's answer for this implementation request")
    initialize.add_argument("--run-dir", type=Path)
    for command in ("present", "review", "approve", "accept-design", "agent-result", "resolve-agent", "check-record", "check"):
        sub = commands.add_parser(command)
        sub.add_argument("--run-dir", type=Path, required=True)
        if command in {"present", "review", "approve", "agent-result", "resolve-agent", "check-record"}:
            sub.add_argument("--area", choices=("frontend", "backend", "integration"), required=True)
        if command == "review":
            sub.add_argument("--result", choices=("clear", "changes-required"), required=True)
            sub.add_argument("--finding", default="")
            sub.add_argument("--slot", type=int, choices=REVIEW_SLOTS)
            sub.add_argument("--agent-id")
        elif command in {"approve", "accept-design"}:
            sub.add_argument("--reference", required=True, help="Reference to the user's actual approval")
        elif command == "agent-result":
            sub.add_argument("--stage", choices=("code-review", "qa"), required=True)
            sub.add_argument("--slot", type=int, choices=REVIEW_SLOTS, required=True)
            sub.add_argument("--agent-id", required=True)
            sub.add_argument("--result", choices=("clear", "findings", "pass", "fail", "not-run"), required=True)
        elif command == "resolve-agent":
            sub.add_argument("--stage", choices=("code-review", "qa"), required=True)
            sub.add_argument("--slot", type=int, choices=REVIEW_SLOTS, required=True)
            sub.add_argument("--reference", required=True, help="Evidence that the finding was resolved")
        elif command == "check-record":
            sub.add_argument("--name", required=True)
            sub.add_argument("--status", choices=("pass", "fail", "not-run"), required=True)
            sub.add_argument("--evidence", required=True)
        elif command == "check":
            sub.add_argument("--gate", choices=("design", "final"), required=True)
    args = parser.parse_args()

    try:
        if args.command == "init":
            if not args.mode_reference.strip():
                raise ValueError("--mode-reference must identify the user's answer for this request")
            repo_root = args.repo.resolve()
            if not (repo_root / ".git").exists():
                raise ValueError("--repo must be a Git checkout")
            run_dir = (args.run_dir or default_run_dir(repo_root)).resolve()
            ensure_local_run_dir(run_dir, repo_root)
            run_dir.mkdir(parents=True, exist_ok=False)
            run_dir.chmod(0o700)
            write_state(run_dir, {"version": 3, "repo_root": str(repo_root), "scope": args.scope,
                                  "review_mode": args.review_mode,
                                  "mode_reference": args.mode_reference, "events": []})
            print(run_dir)
            return 0

        run_dir = args.run_dir.resolve()
        state = read_state(run_dir)
        if args.command in {"present", "review", "approve", "agent-result", "resolve-agent", "check-record"} and args.area not in review_areas(state["scope"]):
            raise ValueError(f"area {args.area} is outside the {state['scope']} scope")
        if args.command == "present":
            if state["review_mode"] != "user-review":
                raise ValueError("present is only required in user-review mode")
            if args.area == "integration":
                raise ValueError("present each design document separately")
            state["events"].append({"type": "present", "area": args.area,
                                    "digest": design_digest(run_dir, args.area)})
            write_state(run_dir, state)
        elif args.command == "review":
            if args.result == "changes-required" and not args.finding.strip():
                raise ValueError("--finding is required for changes-required review")
            if state["version"] == 3:
                if args.slot not in REVIEW_SLOTS or not args.agent_id or not args.agent_id.strip():
                    raise ValueError("v3 design review requires --slot and --agent-id")
                review_file = agent_artifact("design-review", args.area, args.slot)
                state["events"].append({"type": "review", "area": args.area, "slot": args.slot,
                                        "agent_id": args.agent_id.strip(), "result": args.result,
                                        "finding": args.finding, "digest": design_digest(run_dir, args.area),
                                        "artifact_digest": artifact_digest(run_dir, review_file)})
            else:
                review_file = f"{args.area}-design-review.md" if args.area != "integration" else "integration-contract-review.md"
                require_artifact(run_dir, review_file)
                state["events"].append({"type": "review", "area": args.area, "result": args.result,
                                        "finding": args.finding, "digest": design_digest(run_dir, args.area)})
            write_state(run_dir, state)
        elif args.command == "approve":
            if not args.reference.strip():
                raise ValueError("approval reference cannot be empty")
            if state["version"] == 3:
                digest = design_digest(run_dir, args.area)
                latest = latest_slot_results(state, "review", args.area, digest)
                if not any(event["result"] == "changes-required" for event in latest.values()):
                    raise ValueError("no design finding for this area")
                last_finding = max(index for index, event in enumerate(state["events"])
                                   if event["type"] == "review" and event["area"] == args.area
                                   and event["digest"] == digest and event["result"] == "changes-required")
                if any(event["type"] == "approval" and event["area"] == args.area
                       and event["digest"] == digest for event in state["events"][last_finding + 1:]):
                    raise ValueError("design findings already approved")
                state["events"].append({"type": "approval", "area": args.area,
                                        "reference": args.reference, "digest": digest})
            else:
                events = [event for event in state["events"] if event.get("area") == args.area]
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
        elif args.command == "accept-design":
            if state["review_mode"] != "user-review":
                raise ValueError("accept-design is only required in user-review mode")
            if not args.reference.strip():
                raise ValueError("acceptance reference cannot be empty")
            check_reviews(run_dir, state)
            state["events"].append({"type": "accept-design", "reference": args.reference,
                                    "digests": {area: design_digest(run_dir, area)
                                                for area in areas_for(state["scope"])}})
            write_state(run_dir, state)
        elif args.command == "agent-result":
            if state["version"] != 3:
                raise ValueError("agent-result is only available for v3 runs")
            if not args.agent_id.strip():
                raise ValueError("agent id cannot be empty")
            if args.stage == "code-review":
                if args.area == "integration" or args.result not in {"clear", "findings"}:
                    raise ValueError("code-review requires a frontend/backend area and clear/findings result")
                check_design(run_dir, state)
            else:
                if args.result not in {"pass", "fail", "not-run"}:
                    raise ValueError("QA result must be pass, fail, or not-run")
            current_code = code_digest(Path(state["repo_root"]))
            if args.stage == "qa":
                if args.area == "integration":
                    for area in areas_for(state["scope"]):
                        check_agent_stage(run_dir, state, "qa", area, current_code)
                else:
                    check_agent_stage(run_dir, state, "code-review", args.area, current_code)
            report = agent_artifact(args.stage, args.area, args.slot)
            state["events"].append({"type": args.stage, "area": args.area,
                                    "slot": args.slot, "agent_id": args.agent_id.strip(),
                                    "result": args.result, "digest": current_code,
                                    "design_digest": design_digest(run_dir, args.area),
                                    "artifact_digest": artifact_digest(run_dir, report)})
            write_state(run_dir, state)
        elif args.command == "resolve-agent":
            if state["version"] != 3 or not args.reference.strip():
                raise ValueError("v3 agent resolution requires a nonempty reference")
            current_code = code_digest(Path(state["repo_root"]))
            latest = next((event for event in reversed(state["events"])
                           if event["type"] == args.stage and event["area"] == args.area
                           and event["slot"] == args.slot and event["digest"] == current_code), None)
            if latest is None or latest["result"] not in {"findings", "fail", "not-run"} \
                    or latest["design_digest"] != design_digest(run_dir, args.area):
                raise ValueError("no current agent finding to resolve")
            state["events"].append({"type": "agent-resolution", "stage": args.stage,
                                    "area": args.area, "slot": args.slot, "digest": current_code,
                                    "design_digest": design_digest(run_dir, args.area),
                                    "reference": args.reference})
            write_state(run_dir, state)
        elif args.command == "check-record":
            if not args.name.strip() or not args.evidence.strip():
                raise ValueError("check name and evidence cannot be empty")
            event = {"type": "check", "area": args.area, "name": args.name,
                     "status": args.status, "evidence": args.evidence}
            if state["version"] == 3:
                event["digest"] = code_digest(Path(state["repo_root"]))
                event["design_digest"] = design_digest(run_dir, args.area)
            state["events"].append(event)
            write_state(run_dir, state)
        else:
            (check_design if args.gate == "design" else check_final)(run_dir, state)
            print(f"{args.gate} gate passed")
        return 0
    except (OSError, ValueError, json.JSONDecodeError, KeyError, subprocess.CalledProcessError) as error:
        print(f"workflow harness: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
