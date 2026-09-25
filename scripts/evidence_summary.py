#!/usr/bin/env python3
"""Summarize a captured check without changing its result."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from workflow_harness import evidence_digest, lexical_absolute


def read_manifest(run_dir: Path, manifest_file: str) -> tuple[dict, str, str]:
    manifest_path, manifest_hash = evidence_digest(run_dir, manifest_file)
    manifest = json.loads((run_dir / manifest_path).read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("schema_version") not in {1, 2}:
        raise ValueError("invalid check manifest schema")
    required = ("attempt_id", "command", "started_at", "finished_at", "exit_code",
                "status", "log_file", "log_sha256", "repo_root", "area", "code_digest")
    if any(key not in manifest for key in required):
        raise ValueError("check manifest is incomplete")
    if manifest["schema_version"] == 2 and any(
            key not in manifest for key in ("run_id", "checkout_id", "design_digest", "plan_digests")):
        raise ValueError("v7 check manifest is incomplete")
    if not re.fullmatch(r"[0-9a-f]{32}", str(manifest["attempt_id"])):
        raise ValueError("invalid attempt ID")
    if not isinstance(manifest["command"], str) or not manifest["command"].strip():
        raise ValueError("invalid command")
    if not isinstance(manifest["repo_root"], str) or not manifest["repo_root"] \
            or manifest["area"] not in {"frontend", "backend", "integration"} \
            or not isinstance(manifest["code_digest"], str) \
            or not re.fullmatch(r"[0-9a-f]{64}", manifest["code_digest"]):
        raise ValueError("invalid runner code identity")
    if type(manifest["exit_code"]) is not int or manifest["status"] not in {"pass", "fail"}:
        raise ValueError("invalid check result")
    if (manifest["exit_code"] == 0) != (manifest["status"] == "pass"):
        raise ValueError("check status conflicts with exit code")
    log_path, log_hash = evidence_digest(run_dir, str(run_dir / manifest["log_file"]))
    if log_path != manifest["log_file"] or log_hash != manifest["log_sha256"]:
        raise ValueError("check log hash does not match manifest")
    return manifest, manifest_path, manifest_hash


def summarize(run_dir: Path, manifest_file: str, max_lines: int = 10) -> dict:
    run_dir = lexical_absolute(run_dir)
    if max_lines < 1:
        raise ValueError("max_lines must be positive")
    manifest, manifest_path, manifest_hash = read_manifest(run_dir, manifest_file)
    lines = (run_dir / manifest["log_file"]).read_text(encoding="utf-8", errors="replace").splitlines()
    failures = [line for line in lines if re.search(r"(?i)fail|error|traceback|exception", line)]
    excerpt = failures[:max_lines] if failures else lines[:max_lines]
    return {
        "attempt_id": manifest["attempt_id"], "command": manifest["command"],
        "status": manifest["status"], "exit_code": manifest["exit_code"],
        "started_at": manifest["started_at"], "finished_at": manifest["finished_at"],
        "repo_root": manifest["repo_root"], "area": manifest["area"],
        "code_digest": manifest["code_digest"],
        "manifest_file": manifest_path, "manifest_sha256": manifest_hash,
        "log_file": manifest["log_file"], "log_sha256": manifest["log_sha256"],
        "excerpt": excerpt, "omitted_lines": max(0, len(lines) - len(excerpt)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--manifest-file", required=True)
    parser.add_argument("--max-lines", type=int, default=10)
    args = parser.parse_args()
    try:
        print(json.dumps(summarize(args.run_dir, args.manifest_file, args.max_lines),
                         ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"evidence_summary: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
