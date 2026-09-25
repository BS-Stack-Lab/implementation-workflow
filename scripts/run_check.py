#!/usr/bin/env python3
"""Run one planned command and retain an immutable local attempt record."""

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


def capture(run_dir: Path, repo: Path, command: str, timeout: int = 600,
            area: str | None = None) -> Path:
    from workflow_harness import (checkout_identity, code_digest_for, design_digest,
                                  ensure_local_run_dir, lexical_absolute, plan_digests,
                                  read_state, repo_for_area, review_areas)

    run_dir = lexical_absolute(run_dir)
    repo = repo.resolve()
    ensure_local_run_dir(run_dir, repo, allow_legacy=True)
    if not run_dir.is_dir() or not command.strip() or timeout < 1:
        raise ValueError("run directory, command, and positive timeout are required")
    state = read_state(run_dir)
    if area is None and state["scope"] != "both":
        area = state["scope"]
    if area not in review_areas(state["scope"]):
        raise ValueError("--area must name an active workflow area")
    if repo_for_area(state, area).resolve() != repo:
        raise ValueError("runner repository differs from workflow area repository")
    code_digest = code_digest_for(run_dir, state, area)
    attempt_id = uuid.uuid4().hex
    started_at = datetime.now(timezone.utc).isoformat()
    try:
        result = subprocess.run(command, shell=True, cwd=repo, capture_output=True,
                                text=True, timeout=timeout, errors="replace")
        exit_code = result.returncode
        output = result.stdout + result.stderr
        status = "pass" if exit_code == 0 else "fail"
    except subprocess.TimeoutExpired as error:
        exit_code = 124
        output = (error.stdout or b"") + (error.stderr or b"")
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        status = "fail"
        output += "\n[command timed out]\n"
    log_name = f"check-{attempt_id}.log"
    log = run_dir / log_name
    log.write_text(output if output else "[command produced no output]\n", encoding="utf-8")
    log.chmod(0o600)
    manifest = {
        "schema_version": 2 if state["version"] >= 7 else 1,
        "attempt_id": attempt_id,
        "command": command,
        "repo_root": str(repo),
        "area": area,
        "code_digest": code_digest,
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "exit_code": exit_code,
        "status": status,
        "log_file": log_name,
        "log_sha256": hashlib.sha256(log.read_bytes()).hexdigest(),
    }
    if state["version"] >= 7:
        manifest.update({"run_id": state["run_id"],
                         "checkout_id": checkout_identity(repo),
                         "design_digest": design_digest(run_dir, area),
                         "plan_digests": plan_digests(run_dir)})
    target = run_dir / f"check-{attempt_id}.json"
    temporary = run_dir / f".check-{attempt_id}.tmp"
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    os.replace(temporary, target)
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--command", required=True)
    parser.add_argument("--area", choices=("frontend", "backend", "integration"))
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()
    try:
        print(capture(args.run_dir, args.repo, args.command, args.timeout, args.area))
        return 0
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"run_check: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
