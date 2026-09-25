#!/usr/bin/env python3
"""Record the visible weekly Codex usage percentage without claiming precision."""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from workflow_harness import lexical_absolute, read_state


def record(run_dir: Path, label: str, used_percent: int | None) -> dict:
    run_dir = lexical_absolute(run_dir)
    state = read_state(run_dir)
    if state["version"] < 6 or not label.strip():
        raise ValueError("v6 run and a nonempty checkpoint label are required")
    if used_percent is not None and (type(used_percent) is not int or not 0 <= used_percent <= 100):
        raise ValueError("weekly used percent must be an integer from 0 to 100")
    path = run_dir / "usage-checkpoints.jsonl"
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError("usage checkpoints must be a regular local file")
    previous = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    baseline = next((item["used_percent"] for item in previous
                     if item.get("used_percent") is not None), used_percent)
    if used_percent is None or baseline is None or used_percent < baseline:
        status = "unverified"
    elif used_percent - baseline >= 1:
        status = "stop-next-optional-call"
    else:
        status = "below-display-resolution"
    event = {"id": uuid.uuid4().hex, "at": datetime.now(timezone.utc).isoformat(),
             "label": label.strip(), "used_percent": used_percent,
             "baseline_percent": baseline, "visible_delta_points":
             used_percent - baseline if used_percent is not None and baseline is not None
             and used_percent >= baseline else None, "status": status,
             "limit": "Shared integer weekly usage cannot prove per-task usage below 1%."}
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "a", encoding="utf-8") as target:
        target.write(json.dumps(event, ensure_ascii=False) + "\n")
    return event


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--label", required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--used-percent", type=int)
    group.add_argument("--unavailable", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(record(args.run_dir, args.label,
                                None if args.unavailable else args.used_percent),
                         ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(f"usage checkpoint: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
