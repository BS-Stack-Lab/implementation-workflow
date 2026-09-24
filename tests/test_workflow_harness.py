"""Exercise the real local artifact and approval gates."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "scripts" / "workflow_harness.py"
sys.path.insert(0, str(ROOT / "scripts"))
from orchestrate import build_plan  # noqa: E402


class WorkflowHarnessTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.repo = self.base / "target-repo"
        (self.repo / ".git").mkdir(parents=True)
        self.run_dir = self.base / ".codex" / "implementation-workflow-runs" / "one"

    def call(self, *args: str, expected: int = 0) -> subprocess.CompletedProcess[str]:
        result = subprocess.run([sys.executable, str(HARNESS), *args], capture_output=True,
                                text=True, env={**os.environ, "HOME": str(self.base)})
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def init(self, scope: str) -> None:
        self.call("init", "--repo", str(self.repo), "--scope", scope, "--run-dir", str(self.run_dir))

    def write(self, name: str, content: str = "evidence\n") -> None:
        (self.run_dir / name).write_text(content, encoding="utf-8")

    def test_frontend_scope_and_changed_design(self) -> None:
        self.init("frontend")
        self.write("frontend-design.md")
        self.write("frontend-design-review.md")
        self.call("review", "--run-dir", str(self.run_dir), "--area", "frontend", "--result", "clear")
        self.call("check", "--run-dir", str(self.run_dir), "--gate", "design")
        self.write("frontend-design.md", "revised design\n")
        self.call("check", "--run-dir", str(self.run_dir), "--gate", "design", expected=1)
        self.call("review", "--run-dir", str(self.run_dir), "--area", "frontend", "--result", "clear")
        self.write("frontend-code-review.md")
        self.write("frontend-qa.md")
        self.write("final-report.md")
        self.call("check-record", "--run-dir", str(self.run_dir), "--area", "frontend",
                  "--name", "unit tests", "--status", "pass", "--evidence", "test output")
        self.call("check", "--run-dir", str(self.run_dir), "--gate", "final")
        self.assertFalse((self.run_dir / "backend-design.md").exists())

    def test_design_finding_needs_approval_and_new_clear_review(self) -> None:
        self.init("backend")
        self.write("backend-design.md", "first design\n")
        self.write("backend-design-review.md", "issue found\n")
        self.call("review", "--run-dir", str(self.run_dir), "--area", "backend",
                  "--result", "changes-required", "--finding", "missing error case")
        self.call("check", "--run-dir", str(self.run_dir), "--gate", "design", expected=1)
        self.write("backend-design.md", "revised before approval\n")
        self.call("approve", "--run-dir", str(self.run_dir), "--area", "backend",
                  "--reference", "approval came too late", expected=1)
        self.write("backend-design.md", "first design\n")
        self.call("approve", "--run-dir", str(self.run_dir), "--area", "backend",
                  "--reference", "user approved the error case revision")
        self.write("backend-design.md", "revised design\n")
        self.call("check", "--run-dir", str(self.run_dir), "--gate", "design", expected=1)
        self.call("review", "--run-dir", str(self.run_dir), "--area", "backend", "--result", "clear")
        self.call("check", "--run-dir", str(self.run_dir), "--gate", "design")

    def test_both_scope_requires_contract_review_and_integration_qa(self) -> None:
        self.init("both")
        for area in ("frontend", "backend"):
            self.write(f"{area}-design.md")
            self.write(f"{area}-design-review.md")
            self.call("review", "--run-dir", str(self.run_dir), "--area", area, "--result", "clear")
        self.call("check", "--run-dir", str(self.run_dir), "--gate", "design", expected=1)
        self.write("integration-contract-review.md")
        self.call("review", "--run-dir", str(self.run_dir), "--area", "integration", "--result", "clear")
        self.call("check", "--run-dir", str(self.run_dir), "--gate", "design")
        for area in ("frontend", "backend"):
            self.write(f"{area}-code-review.md")
            self.write(f"{area}-qa.md")
            self.call("check-record", "--run-dir", str(self.run_dir), "--area", area,
                      "--name", "tests", "--status", "pass", "--evidence", "test output")
        self.write("final-report.md")
        self.call("check", "--run-dir", str(self.run_dir), "--gate", "final", expected=1)
        self.write("integration-qa.md")
        self.call("check-record", "--run-dir", str(self.run_dir), "--area", "integration",
                  "--name", "contract test", "--status", "pass", "--evidence", "API response")
        self.call("check", "--run-dir", str(self.run_dir), "--gate", "final")

    def test_run_directory_cannot_be_inside_git_repo(self) -> None:
        nested_repo = self.base / ".codex" / "implementation-workflow-runs" / "nested-repo"
        (nested_repo / ".git").mkdir(parents=True)
        result = self.call("init", "--repo", str(nested_repo), "--scope", "frontend",
                           "--run-dir", str(nested_repo / "reports"), expected=1)
        self.assertIn("outside the target Git repository", result.stderr)

    def test_run_directory_must_use_local_base(self) -> None:
        result = self.call("init", "--repo", str(self.repo), "--scope", "backend",
                           "--run-dir", str(self.base / "Dropbox" / "run"), expected=1)
        self.assertIn("under ~/.codex/implementation-workflow-runs", result.stderr)

    def test_artifact_symlink_to_repository_is_rejected(self) -> None:
        self.init("frontend")
        outside = self.repo / "design.md"
        outside.write_text("should stay outside the repo\n", encoding="utf-8")
        (self.run_dir / "frontend-design.md").symlink_to(outside)
        self.write("frontend-design-review.md")
        result = self.call("review", "--run-dir", str(self.run_dir), "--area", "frontend",
                           "--result", "clear", expected=1)
        self.assertIn("artifact must be a local file", result.stderr)

    def test_scope_aware_plan(self) -> None:
        frontend = {task["id"] for task in build_plan("frontend")}
        both = {task["id"] for task in build_plan("both")}
        self.assertIn("qa-frontend", frontend)
        self.assertNotIn("qa-backend", frontend)
        self.assertNotIn("review-contract", frontend)
        self.assertIn("qa-backend", both)
        self.assertIn("review-contract", both)
        self.assertIn("qa-integration", both)


if __name__ == "__main__":
    unittest.main()
