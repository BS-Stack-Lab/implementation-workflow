from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
import os
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "guard_documents.py"


class DocumentGuardTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.git("init", "-q")
        empty_hooks = self.repo / "empty-hooks"
        empty_hooks.mkdir()
        self.git("config", "core.hooksPath", str(empty_hooks))
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.invalid")
        self.write("README.md", "initial\n")
        self.git("add", "README.md")
        self.git("commit", "-qm", "Initial")
        self.base = self.git("rev-parse", "HEAD").stdout.strip()

    def git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args], cwd=self.repo, text=True, capture_output=True, check=True
        )

    def write(self, name: str, content: str) -> None:
        target = self.repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def guard(self, *args: str, input_text: str | None = None,
              approved_repo: str | None = None,
              approved_paths: str | None = None) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        if approved_repo is not None:
            env["DOCUMENT_GUARD_APPROVED_REPO"] = approved_repo
        if approved_paths is not None:
            env["DOCUMENT_GUARD_APPROVED_PATHS"] = approved_paths
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            cwd=self.repo, text=True, capture_output=True, input=input_text, env=env,
        )

    def test_staged_document_is_blocked_and_readme_is_allowed(self) -> None:
        self.write("docs/design.md", "draft\n")
        self.git("add", "docs/design.md")
        result = self.guard("--staged")
        self.assertEqual(result.returncode, 1)
        self.assertIn("docs/design.md", result.stderr)

        self.git("reset", "-q", "--", "docs/design.md")
        self.write("README.md", "updated\n")
        self.git("add", "README.md")
        self.assertEqual(self.guard("--staged").returncode, 0)

    def test_committed_document_is_blocked_in_diff(self) -> None:
        self.write("SKILL.md", "instructions\n")
        self.git("add", "SKILL.md")
        self.git("commit", "-qm", "Add skill")
        result = self.guard("--diff", self.base, "HEAD")
        self.assertEqual(result.returncode, 1)
        self.assertIn("SKILL.md", result.stderr)

    def test_document_deletion_is_blocked(self) -> None:
        self.write("docs/notes.txt", "notes\n")
        self.git("add", "docs/notes.txt")
        self.git("commit", "-qm", "Add notes")
        self.git("rm", "-q", "docs/notes.txt")
        self.assertEqual(self.guard("--staged").returncode, 1)

    def test_pre_push_rejects_committed_document(self) -> None:
        self.write("design.pdf", "example\n")
        self.git("add", "design.pdf")
        self.git("commit", "-qm", "Add design")
        head = self.git("rev-parse", "HEAD").stdout.strip()
        line = f"refs/heads/master {head} refs/heads/master {self.base}\n"
        result = self.guard("--pre-push", "origin", input_text=line)
        self.assertEqual(result.returncode, 1)
        self.assertIn("design.pdf", result.stderr)

    def test_explicit_exception_is_limited_to_repo_and_exact_path(self) -> None:
        self.write("skills/example/SKILL.md", "instructions\n")
        self.write("docs/design.md", "draft\n")
        self.git("add", "skills/example/SKILL.md", "docs/design.md")
        result = self.guard("--staged", approved_repo=str(self.repo),
                            approved_paths="skills/example/SKILL.md")
        self.assertEqual(result.returncode, 1)
        self.assertIn("docs/design.md", result.stderr)
        self.assertNotIn("skills/example/SKILL.md", result.stderr)
        result = self.guard("--staged", approved_repo=str(self.repo / "other"),
                            approved_paths="skills/example/SKILL.md")
        self.assertEqual(result.returncode, 1)
        self.assertIn("skills/example/SKILL.md", result.stderr)


if __name__ == "__main__":
    unittest.main()
