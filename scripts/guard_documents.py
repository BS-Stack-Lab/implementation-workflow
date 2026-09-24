#!/usr/bin/env python3
"""Reject documentation changes, except README.md, before Git writes."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path, PurePosixPath


DOCUMENT_SUFFIXES = {
    ".adoc", ".asciidoc", ".doc", ".docx", ".epub", ".markdown",
    ".md", ".mdx", ".odt", ".pages", ".pdf", ".rst", ".rtf", ".txt",
}
DOCUMENT_DIRS = {"docs", "documentation", "design-docs", "reports"}


def is_blocked_document(raw_path: str) -> bool:
    path = PurePosixPath(raw_path)
    if path.name.casefold() == "readme.md":
        return False
    if path.suffix.casefold() in DOCUMENT_SUFFIXES:
        return True
    return any(part.casefold() in DOCUMENT_DIRS for part in path.parts[:-1])


def explicitly_approved_paths() -> set[str]:
    """Return a one-command, exact-repository exception for user-requested files."""
    approved_repo = os.environ.get("DOCUMENT_GUARD_APPROVED_REPO")
    raw_paths = os.environ.get("DOCUMENT_GUARD_APPROVED_PATHS")
    if not approved_repo or not raw_paths:
        return set()
    actual_repo = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    if Path(approved_repo).resolve() != Path(actual_repo).resolve():
        return set()
    return {path for path in raw_paths.split(":") if path and not Path(path).is_absolute()}


def git_paths(*args: str) -> list[str]:
    result = subprocess.run(
        ["git", *args], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    return [part.decode("utf-8", "surrogateescape") for part in result.stdout.split(b"\0") if part]


def staged_paths() -> list[str]:
    return git_paths("diff", "--cached", "--name-only", "-z", "--no-renames")


def changed_paths(base: str, head: str) -> list[str]:
    return git_paths("diff", "--name-only", "-z", "--no-renames", base, head, "--")


def pushed_paths(remote: str) -> list[str]:
    zero = "0" * 40
    commits: set[str] = set()
    for line in sys.stdin:
        fields = line.split()
        if len(fields) != 4:
            raise ValueError("invalid pre-push input")
        _, local_sha, _, remote_sha = fields
        if local_sha == zero:
            continue  # Deleting a ref does not upload document changes.
        if remote_sha == zero:
            revs = ["rev-list", local_sha, "--not", f"--remotes={remote}"]
        else:
            revs = ["rev-list", f"{remote_sha}..{local_sha}"]
        result = subprocess.run(["git", *revs], check=True, capture_output=True, text=True)
        commits.update(result.stdout.splitlines())
    paths: set[str] = set()
    for commit in commits:
        paths.update(git_paths(
            "diff-tree", "--root", "-m", "-r", "--no-commit-id", "--name-only",
            "-z", "--no-renames", commit,
        ))
    return sorted(paths)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--staged", action="store_true")
    group.add_argument("--diff", nargs=2, metavar=("BASE", "HEAD"))
    group.add_argument("--pre-push", metavar="REMOTE")
    args = parser.parse_args()
    try:
        if args.staged:
            paths = staged_paths()
        elif args.diff:
            paths = changed_paths(*args.diff)
        else:
            paths = pushed_paths(args.pre_push)
    except (subprocess.CalledProcessError, ValueError) as error:
        if isinstance(error, ValueError):
            print(error, file=sys.stderr)
            return 2
        stderr = error.stderr
        sys.stderr.write(stderr.decode("utf-8", "replace") if isinstance(stderr, bytes) else stderr)
        return 2
    try:
        approved = explicitly_approved_paths()
    except subprocess.CalledProcessError:
        approved = set()
    blocked = sorted(set(path for path in paths if is_blocked_document(path) and path not in approved))
    if not blocked:
        return 0
    print("Blocked document changes (only README.md is allowed):", file=sys.stderr)
    for path in blocked:
        print(f"  {path}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
