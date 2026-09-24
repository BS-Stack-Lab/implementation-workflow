"""Exercise local artifacts, three-agent gates, and v2 compatibility."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "scripts" / "workflow_harness.py"
sys.path.insert(0, str(ROOT / "scripts"))
from orchestrate import build_plan  # noqa: E402
from workflow_harness import directory_id  # noqa: E402


class WorkflowHarnessTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.repo = self.base / "target-repo"
        self.repo.mkdir()
        subprocess.run(["git", "-C", str(self.repo), "init", "-q"], check=True)
        (self.repo / "app.py").write_text("value = 1\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repo), "add", "app.py"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "-c", "core.hooksPath=/dev/null",
                        "-c", "user.name=Test", "-c", "user.email=test@example.test",
                        "commit", "-qm", "initial"], check=True)
        self.docs = self.base / "Documents" / "docs"
        self.run_dir = self.docs / "one"

    def call(self, *args: str, expected: int = 0) -> subprocess.CompletedProcess[str]:
        result = subprocess.run([sys.executable, str(HARNESS), *args], capture_output=True,
                                text=True, env={**os.environ, "HOME": str(self.base)})
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def init(self, scope: str, review_mode: str = "immediate") -> None:
        self.call("init", "--repo", str(self.repo), "--scope", scope,
                  "--review-mode", review_mode, "--mode-reference", "user answered for this request",
                  "--run-dir", str(self.run_dir))

    def write(self, name: str, content: str = "evidence\n") -> None:
        (self.run_dir / name).write_text(content, encoding="utf-8")

    def review(self, area: str, slot: int, result: str = "clear", agent_id: str | None = None) -> None:
        stem = "integration-contract-review" if area == "integration" else f"{area}-design-review"
        self.write(f"{stem}-{slot}.md", f"{result} by {slot}\n")
        arguments = ["review", "--run-dir", str(self.run_dir), "--area", area,
                     "--slot", str(slot), "--agent-id", agent_id or f"/root/design-{area}-{slot}",
                     "--result", result]
        if result == "changes-required":
            arguments += ["--finding", "missing error case"]
        self.call(*arguments)

    def reviews(self, area: str) -> None:
        for slot in (1, 2, 3):
            self.review(area, slot)

    def agent_result(self, stage: str, area: str, slot: int, result: str | None = None,
                     agent_id: str | None = None) -> None:
        result = result or ("clear" if stage == "code-review" else "pass")
        self.write(f"{area}-{stage if stage == 'code-review' else 'qa'}-{slot}.md",
                   f"{result} by {slot}\n")
        self.call("agent-result", "--run-dir", str(self.run_dir), "--stage", stage,
                  "--area", area, "--slot", str(slot),
                  "--agent-id", agent_id or f"/root/{stage}-{area}-{slot}",
                  "--result", result)

    def agent_results(self, stage: str, area: str) -> None:
        for slot in (1, 2, 3):
            self.agent_result(stage, area, slot)

    def check_record(self, area: str) -> None:
        self.call("check-record", "--run-dir", str(self.run_dir), "--area", area,
                  "--name", "tests", "--status", "pass", "--evidence", "test output")

    def resolve_agent(self, stage: str, area: str, slot: int) -> None:
        self.call("resolve-agent", "--run-dir", str(self.run_dir), "--stage", stage,
                  "--area", area, "--slot", str(slot),
                  "--reference", "issue repaired or false positive documented")

    def test_init_requires_current_request_choice_reference(self) -> None:
        self.call("init", "--repo", str(self.repo), "--scope", "frontend",
                  "--review-mode", "immediate", "--run-dir", str(self.run_dir), expected=2)
        self.call("init", "--repo", str(self.repo), "--scope", "frontend",
                  "--review-mode", "immediate", "--mode-reference", " ",
                  "--run-dir", str(self.run_dir), expected=1)
        self.init("frontend")
        state = json.loads((self.run_dir / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["version"], 3)
        self.assertEqual(state["mode_reference"], "user answered for this request")

    def test_three_design_code_and_qa_results_are_required(self) -> None:
        self.init("frontend")
        self.write("frontend-design.md")
        self.write("frontend-design-review.md")
        for slot in (1, 2):
            self.review("frontend", slot)
        self.call("check", "--run-dir", str(self.run_dir), "--gate", "design", expected=1)
        self.review("frontend", 3)
        self.call("check", "--run-dir", str(self.run_dir), "--gate", "design")
        self.write("frontend-code-review.md")
        self.write("frontend-qa.md")
        self.write("final-report.md")
        self.check_record("frontend")
        for slot in (1, 2):
            self.agent_result("code-review", "frontend", slot)
        self.call("check", "--run-dir", str(self.run_dir), "--gate", "final", expected=1)
        self.agent_result("code-review", "frontend", 3)
        self.agent_results("qa", "frontend")
        self.call("check", "--run-dir", str(self.run_dir), "--gate", "final")
        self.assertFalse((self.run_dir / "backend-design.md").exists())

    def test_user_review_requires_current_design_acceptance(self) -> None:
        self.init("frontend", "user-review")
        self.write("frontend-design.md", "first design\n")
        self.write("frontend-design-review.md")
        self.reviews("frontend")
        self.call("check", "--run-dir", str(self.run_dir), "--gate", "design", expected=1)
        self.call("present", "--run-dir", str(self.run_dir), "--area", "frontend")
        self.call("accept-design", "--run-dir", str(self.run_dir), "--reference", "user approved draft")
        self.call("check", "--run-dir", str(self.run_dir), "--gate", "design")
        self.write("frontend-design.md", "revised design\n")
        self.call("check", "--run-dir", str(self.run_dir), "--gate", "design", expected=1)
        self.call("present", "--run-dir", str(self.run_dir), "--area", "frontend")
        self.reviews("frontend")
        self.call("check", "--run-dir", str(self.run_dir), "--gate", "design", expected=1)
        self.call("accept-design", "--run-dir", str(self.run_dir), "--reference", "user approved revision")
        self.call("check", "--run-dir", str(self.run_dir), "--gate", "design")

    def test_design_finding_requires_batch_approval_and_new_digest(self) -> None:
        self.init("backend")
        self.write("backend-design.md", "first design\n")
        self.write("backend-design-review.md")
        self.review("backend", 1, "changes-required")
        self.review("backend", 2)
        self.call("approve", "--run-dir", str(self.run_dir), "--area", "backend",
                  "--reference", "too early", expected=1)
        self.review("backend", 3)
        self.call("check", "--run-dir", str(self.run_dir), "--gate", "design", expected=1)
        self.call("approve", "--run-dir", str(self.run_dir), "--area", "backend",
                  "--reference", "user approved all three findings")
        self.reviews("backend")
        self.call("check", "--run-dir", str(self.run_dir), "--gate", "design", expected=1)
        self.write("backend-design.md", "revised design\n")
        self.reviews("backend")
        self.call("check", "--run-dir", str(self.run_dir), "--gate", "design")

    def test_duplicate_agent_and_changed_artifact_are_rejected(self) -> None:
        self.init("backend")
        self.write("backend-design.md")
        self.write("backend-design-review.md")
        self.review("backend", 1, agent_id="/root/shared")
        self.review("backend", 2, agent_id="/root/shared")
        self.review("backend", 3)
        self.call("check", "--run-dir", str(self.run_dir), "--gate", "design", expected=1)
        self.review("backend", 2, agent_id="/root/unique")
        self.call("check", "--run-dir", str(self.run_dir), "--gate", "design")
        self.write("backend-design-review-2.md", "tampered\n")
        self.call("check", "--run-dir", str(self.run_dir), "--gate", "design", expected=1)

    def test_both_scope_requires_contract_and_integration_three_results(self) -> None:
        self.init("both")
        for area in ("frontend", "backend"):
            self.write(f"{area}-design.md")
            self.write(f"{area}-design-review.md")
            self.reviews(area)
        self.call("check", "--run-dir", str(self.run_dir), "--gate", "design", expected=1)
        self.write("integration-contract-review.md")
        self.reviews("integration")
        self.call("check", "--run-dir", str(self.run_dir), "--gate", "design")
        for area in ("frontend", "backend"):
            self.write(f"{area}-code-review.md")
            self.write(f"{area}-qa.md")
            self.check_record(area)
            self.agent_results("code-review", area)
            self.agent_results("qa", area)
        self.write("integration-qa.md")
        self.write("final-report.md")
        self.check_record("integration")
        self.call("check", "--run-dir", str(self.run_dir), "--gate", "final", expected=1)
        self.agent_results("qa", "integration")
        self.call("check", "--run-dir", str(self.run_dir), "--gate", "final")

    def test_code_change_invalidates_reviews_qa_and_checks(self) -> None:
        self.init("frontend")
        self.write("frontend-design.md")
        self.write("frontend-design-review.md")
        self.reviews("frontend")
        self.write("frontend-code-review.md")
        self.write("frontend-qa.md")
        self.write("final-report.md")
        self.check_record("frontend")
        self.agent_results("code-review", "frontend")
        self.agent_results("qa", "frontend")
        self.call("check", "--run-dir", str(self.run_dir), "--gate", "final")
        (self.repo / "app.py").write_text("value = 2\n", encoding="utf-8")
        self.call("check", "--run-dir", str(self.run_dir), "--gate", "final", expected=1)
        self.check_record("frontend")
        self.agent_results("code-review", "frontend")
        self.agent_results("qa", "frontend")
        self.call("check", "--run-dir", str(self.run_dir), "--gate", "final")

    def test_design_change_invalidates_code_review_qa_and_checks(self) -> None:
        self.init("frontend")
        self.write("frontend-design.md", "design A\n")
        self.write("frontend-design-review.md")
        self.reviews("frontend")
        self.write("frontend-code-review.md")
        self.write("frontend-qa.md")
        self.write("final-report.md")
        self.check_record("frontend")
        self.agent_results("code-review", "frontend")
        self.agent_results("qa", "frontend")
        self.call("check", "--run-dir", str(self.run_dir), "--gate", "final")
        self.write("frontend-design.md", "design B\n")
        self.reviews("frontend")
        self.call("check", "--run-dir", str(self.run_dir), "--gate", "final", expected=1)
        self.check_record("frontend")
        self.agent_results("code-review", "frontend")
        self.agent_results("qa", "frontend")
        self.call("check", "--run-dir", str(self.run_dir), "--gate", "final")

    def test_qa_failure_is_not_a_completed_gate(self) -> None:
        self.init("frontend")
        self.write("frontend-design.md")
        self.write("frontend-design-review.md")
        self.reviews("frontend")
        self.write("frontend-code-review.md")
        self.write("frontend-qa.md")
        self.write("final-report.md")
        self.check_record("frontend")
        self.agent_results("code-review", "frontend")
        self.agent_result("qa", "frontend", 1, "fail")
        self.agent_result("qa", "frontend", 2)
        self.agent_result("qa", "frontend", 3)
        self.call("check", "--run-dir", str(self.run_dir), "--gate", "final", expected=1)
        self.resolve_agent("qa", "frontend", 1)
        self.agent_result("qa", "frontend", 1, "pass")
        self.call("check", "--run-dir", str(self.run_dir), "--gate", "final")

    def test_code_finding_requires_resolution_before_same_digest_rerun(self) -> None:
        self.init("frontend")
        self.write("frontend-design.md")
        self.write("frontend-design-review.md")
        self.reviews("frontend")
        self.agent_result("code-review", "frontend", 1, "findings")
        self.agent_result("code-review", "frontend", 2)
        self.agent_result("code-review", "frontend", 3)
        self.agent_result("code-review", "frontend", 1, "clear")
        self.write("frontend-qa-1.md")
        self.call("agent-result", "--run-dir", str(self.run_dir), "--stage", "qa",
                  "--area", "frontend", "--slot", "1", "--agent-id", "/root/qa-one",
                  "--result", "pass", expected=1)
        self.agent_result("code-review", "frontend", 1, "findings")
        self.resolve_agent("code-review", "frontend", 1)
        self.agent_result("code-review", "frontend", 1, "clear")
        self.agent_results("qa", "frontend")

    def test_unborn_git_head_can_record_code_state(self) -> None:
        empty_repo = self.base / "new-repo"
        empty_repo.mkdir()
        subprocess.run(["git", "-C", str(empty_repo), "init", "-q"], check=True)
        (empty_repo / "new.py").write_text("value = 1\n", encoding="utf-8")
        self.call("init", "--repo", str(empty_repo), "--scope", "frontend",
                  "--review-mode", "immediate", "--mode-reference", "user answered",
                  "--run-dir", str(self.run_dir))
        self.write("frontend-design.md")
        self.check_record("frontend")

    def test_v2_run_remains_compatible(self) -> None:
        self.init("frontend")
        state_file = self.run_dir / "state.json"
        state = json.loads(state_file.read_text(encoding="utf-8"))
        state["version"] = 2
        state_file.write_text(json.dumps(state), encoding="utf-8")
        self.write("frontend-design.md")
        self.write("frontend-design-review.md")
        self.call("review", "--run-dir", str(self.run_dir), "--area", "frontend", "--result", "clear")
        self.call("check", "--run-dir", str(self.run_dir), "--gate", "design")
        self.write("frontend-code-review.md")
        self.write("frontend-qa.md")
        self.write("final-report.md")
        self.check_record("frontend")
        self.call("check", "--run-dir", str(self.run_dir), "--gate", "final")

    def test_existing_legacy_run_can_continue_but_not_be_initialized(self) -> None:
        self.init("frontend")
        legacy = self.base / ".codex" / "implementation-workflow-runs" / "old-run"
        legacy.parent.mkdir(parents=True)
        self.run_dir.rename(legacy)
        self.run_dir = legacy
        self.write("frontend-design.md")
        self.write("frontend-design-review.md")
        self.reviews("frontend")
        self.call("check", "--run-dir", str(self.run_dir), "--gate", "design")
        self.call("init", "--repo", str(self.repo), "--scope", "frontend",
                  "--review-mode", "immediate", "--mode-reference", "new answer",
                  "--run-dir", str(legacy.parent / "new-run"), expected=1)

    def test_run_directory_cannot_be_inside_git_repo(self) -> None:
        nested_repo = self.docs / "nested-repo"
        (nested_repo / ".git").mkdir(parents=True)
        result = self.call("init", "--repo", str(nested_repo), "--scope", "frontend",
                           "--review-mode", "immediate", "--mode-reference", "user answered",
                           "--run-dir", str(nested_repo / "reports"), expected=1)
        self.assertIn("outside the target Git repository", result.stderr)

    def test_run_directory_must_use_local_base(self) -> None:
        result = self.call("init", "--repo", str(self.repo), "--scope", "backend",
                           "--review-mode", "immediate", "--mode-reference", "user answered",
                           "--run-dir", str(self.base / "Dropbox" / "run"), expected=1)
        self.assertIn("under ~/Documents/docs", result.stderr)

    def test_default_run_uses_repository_branch_and_work_folders(self) -> None:
        first = Path(self.call("init", "--repo", str(self.repo), "--scope", "frontend",
                               "--review-mode", "immediate", "--mode-reference", "answer",
                               "--work-item", "profile page").stdout.strip())
        second = Path(self.call("init", "--repo", str(self.repo), "--scope", "frontend",
                                "--review-mode", "immediate", "--mode-reference", "answer",
                                "--work-item", "profile page").stdout.strip())
        self.assertEqual(first.parent, second.parent)
        self.assertNotEqual(first, second)
        self.assertEqual(first.parents[3], self.docs)
        self.assertTrue(first.name.startswith("run-"))
        self.assertTrue((first / "state.json").exists())

    def test_directory_ids_distinguish_similar_names(self) -> None:
        self.assertNotEqual(directory_id("feature/a", "feature/a"),
                            directory_id("feature-a", "feature-a"))
        self.assertNotEqual(directory_id("project", "https://example.test/a/project"),
                            directory_id("project", "https://example.test/b/project"))

    def test_existing_manual_folder_is_reused_without_overwriting(self) -> None:
        parent = self.docs / "manual-project" / "feat-profile"
        parent.mkdir(parents=True)
        (parent / "old-design.md").write_text("keep\n", encoding="utf-8")
        run = Path(self.call("init", "--repo", str(self.repo), "--scope", "frontend",
                             "--review-mode", "immediate", "--mode-reference", "answer",
                             "--parent-dir", str(parent)).stdout.strip())
        self.assertEqual(run.parent, parent)
        self.assertEqual((parent / "old-design.md").read_text(), "keep\n")

    def test_relative_run_dir_is_reported_as_absolute(self) -> None:
        relative = Path(os.path.relpath(self.docs / "relative-run", Path.cwd()))
        run = Path(self.call("init", "--repo", str(self.repo), "--scope", "frontend",
                             "--review-mode", "immediate", "--mode-reference", "answer",
                             "--run-dir", str(relative)).stdout.strip())
        self.assertTrue(run.is_absolute())
        self.assertEqual(run, self.docs / "relative-run")

    def test_folder_arguments_reject_escape_and_ambiguity(self) -> None:
        for item in ("", "..", "a/b", "a\\b"):
            self.call("init", "--repo", str(self.repo), "--scope", "frontend",
                      "--review-mode", "immediate", "--mode-reference", "answer",
                      "--work-item", item, expected=1)
        outside = self.base / "outside"
        outside.mkdir()
        self.call("init", "--repo", str(self.repo), "--scope", "frontend",
                  "--review-mode", "immediate", "--mode-reference", "answer",
                  "--parent-dir", str(outside), expected=1)
        self.call("init", "--repo", str(self.repo), "--scope", "frontend",
                  "--review-mode", "immediate", "--mode-reference", "answer",
                  "--parent-dir", str(self.docs / "missing"), expected=1)
        self.call("init", "--repo", str(self.repo), "--scope", "frontend",
                  "--review-mode", "immediate", "--mode-reference", "answer",
                  "--parent-dir", str(outside), "--run-dir", str(self.run_dir), expected=1)
        self.call("init", "--repo", str(self.repo), "--scope", "frontend",
                  "--review-mode", "immediate", "--mode-reference", "answer",
                  "--parent-dir", str(self.docs), expected=1)

    def test_symlink_and_git_worktree_folder_are_rejected(self) -> None:
        self.docs.mkdir(parents=True)
        outside = self.base / "outside"
        outside.mkdir()
        (self.docs / "linked").symlink_to(outside, target_is_directory=True)
        self.call("init", "--repo", str(self.repo), "--scope", "frontend",
                  "--review-mode", "immediate", "--mode-reference", "answer",
                  "--parent-dir", str(self.docs / "linked"), expected=1)
        outside_link = self.base / "outside-link"
        outside_link.symlink_to(self.docs, target_is_directory=True)
        self.call("init", "--repo", str(self.repo), "--scope", "frontend",
                  "--review-mode", "immediate", "--mode-reference", "answer",
                  "--run-dir", str(outside_link / "run"), expected=1)
        worktree = self.docs / "worktree"
        worktree.mkdir()
        (worktree / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")
        self.call("init", "--repo", str(self.repo), "--scope", "frontend",
                  "--review-mode", "immediate", "--mode-reference", "answer",
                  "--parent-dir", str(worktree), expected=1)

    def test_symlink_to_existing_run_is_rejected_for_followup(self) -> None:
        self.init("frontend")
        linked = self.docs / "linked-run"
        linked.symlink_to(self.run_dir, target_is_directory=True)
        self.call("check", "--run-dir", str(linked), "--gate", "design", expected=1)

    def test_system_symlink_above_home_does_not_block_docs(self) -> None:
        actual = self.base / "actual"
        home = actual / "home"
        home.mkdir(parents=True)
        alias = self.base / "alias"
        alias.symlink_to(actual, target_is_directory=True)
        result = subprocess.run(
            [sys.executable, str(HARNESS), "init", "--repo", str(self.repo),
             "--scope", "frontend", "--review-mode", "immediate",
             "--mode-reference", "answer"],
            capture_output=True, text=True, env={**os.environ, "HOME": str(alias / "home")})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(Path(result.stdout.strip()).is_dir())
        app = home / "app"
        app.mkdir()
        relative = subprocess.run(
            [sys.executable, str(HARNESS), "init", "--repo", str(self.repo),
             "--scope", "frontend", "--review-mode", "immediate",
             "--mode-reference", "answer", "--run-dir", "../Documents/docs/relative-run"],
            cwd=alias / "home" / "app", capture_output=True, text=True,
            env={**os.environ, "HOME": str(alias / "home")})
        self.assertEqual(relative.returncode, 0, relative.stderr)
        self.assertTrue(Path(relative.stdout.strip()).is_absolute())
        physical = subprocess.run(
            [sys.executable, str(HARNESS), "init", "--repo", str(self.repo),
             "--scope", "frontend", "--review-mode", "immediate",
             "--mode-reference", "answer", "--run-dir",
             str(home / "Documents" / "docs" / "physical-run")],
            capture_output=True, text=True,
            env={**os.environ, "HOME": str(alias / "home")})
        self.assertEqual(physical.returncode, 0, physical.stderr)

    def test_artifact_symlink_to_repository_is_rejected(self) -> None:
        self.init("frontend")
        outside = self.repo / "design.md"
        outside.write_text("should stay outside the repo\n", encoding="utf-8")
        (self.run_dir / "frontend-design.md").symlink_to(outside)
        self.write("frontend-design-review-1.md")
        result = self.call("review", "--run-dir", str(self.run_dir), "--area", "frontend",
                           "--slot", "1", "--agent-id", "/root/one", "--result", "clear", expected=1)
        self.assertIn("artifact must be a local file", result.stderr)

    def test_scope_aware_plan(self) -> None:
        frontend = {task["id"] for task in build_plan("frontend")}
        both = {task["id"] for task in build_plan("both")}
        self.assertIn("qa-frontend-3", frontend)
        self.assertNotIn("qa-backend-1", frontend)
        self.assertNotIn("review-contract-1", frontend)
        self.assertIn("qa-backend-3", both)
        self.assertIn("review-contract-3", both)
        self.assertIn("qa-integration-3", both)
        reviewed = {task["id"] for task in build_plan("both", "user-review")}
        self.assertIn("present-design-frontend", reviewed)
        self.assertIn("present-design-backend", reviewed)
        self.assertIn("accept-design", reviewed)


if __name__ == "__main__":
    unittest.main()
