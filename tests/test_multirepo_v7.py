from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "scripts" / "workflow_harness.py"
RUN_CHECK = ROOT / "scripts" / "run_check.py"
sys.path.insert(0, str(ROOT / "scripts"))
from workflow_harness import checkout_identity, code_digest_for, read_state, risk_guard
from review_brief import changed_paths


class MultiRepoV7Test(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        previous_home = os.environ.get("HOME")
        os.environ["HOME"] = str(self.home)
        self.addCleanup(lambda: os.environ.__setitem__("HOME", previous_home)
                        if previous_home is not None else os.environ.pop("HOME", None))
        self.frontend = self.make_repo("frontend")
        self.backend = self.make_repo("backend")
        self.environment = {**os.environ, "HOME": str(self.home)}

    def make_repo(self, name: str) -> Path:
        repo = self.home / name
        repo.mkdir()
        subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
        (repo / "app.txt").write_text(f"{name} baseline\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "app.txt"], check=True)
        subprocess.run(["git", "-C", str(repo), "-c", "core.hooksPath=/dev/null",
                        "-c", "user.name=Test", "-c", "user.email=test@example.test",
                        "commit", "-qm", "initial"], check=True)
        return repo

    def call(self, script: Path, *args: str, success: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run([sys.executable, str(script), *args], capture_output=True,
                                text=True, env=self.environment)
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def initialize(self, *extra: str) -> Path:
        result = self.call(HARNESS, "init", "--scope", "both",
                           "--frontend-repo", str(self.frontend),
                           "--backend-repo", str(self.backend),
                           "--review-mode", "immediate",
                           "--mode-reference", "user selected immediate for this request",
                           "--review-depth", "full", "--work-item", "account-settings",
                           *extra)
        return Path(result.stdout.strip()).resolve()

    def write_design_and_plans(self, run_dir: Path) -> None:
        for area in ("frontend", "backend"):
            (run_dir / f"{area}-design.md").write_text(
                "## 수용 기준\nR1. 영역별 검사를 실행한다.\n", encoding="utf-8")
        areas = ("frontend", "backend", "integration")
        checks = {area: [{"id": "test", "kind": "test", "command": "true",
                          "required": True}] for area in areas}
        kinds = ("normal", "boundary", "authorization", "regression", "operations")
        cases = {area: [{"slot": index % 3 + 1,
                         "scenario_id": f"{area}-{kind}", "criterion_id": "R1",
                         "source_area": "frontend" if area == "integration" else area,
                         "kind": kind, "steps": "실행", "expected": "통과"}
                        for index, kind in enumerate(kinds)] for area in areas}
        (run_dir / "verification-plan.json").write_text(json.dumps(checks), encoding="utf-8")
        (run_dir / "qa-plan.json").write_text(json.dumps(cases), encoding="utf-8")

    def test_separate_roots_are_recorded_and_resumed_for_same_work_item(self) -> None:
        run_dir = self.initialize()
        state = read_state(run_dir)
        self.assertEqual(state["version"], 7)
        self.assertEqual(Path(state["repo_roots"]["frontend"]), self.frontend)
        self.assertEqual(Path(state["repo_roots"]["backend"]), self.backend)
        self.assertTrue(run_dir.is_relative_to(self.home / "Documents" / "docs"))
        marker = run_dir / "frontend-design.md"
        marker.write_text("existing draft\n", encoding="utf-8")
        self.assertEqual(self.initialize(), run_dir)
        self.assertEqual(marker.read_text(encoding="utf-8"), "existing draft\n")

    def test_committed_change_remains_visible_from_saved_base(self) -> None:
        base = subprocess.run(["git", "-C", str(self.frontend), "rev-parse", "HEAD"],
                              check=True, capture_output=True, text=True).stdout.strip()
        (self.frontend / "app.txt").write_text("committed change\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.frontend), "add", "app.txt"], check=True)
        subprocess.run(["git", "-C", str(self.frontend), "-c", "user.name=Test",
                        "-c", "user.email=test@example.test", "commit", "-qm", "change"],
                       check=True)
        self.assertEqual(changed_paths(self.frontend), [])
        self.assertEqual(changed_paths(self.frontend, base), ["app.txt"])

    def test_light_review_rejects_committed_high_risk_change(self) -> None:
        result = self.call(HARNESS, "init", "--scope", "backend",
                           "--repo", str(self.backend),
                           "--review-mode", "immediate",
                           "--mode-reference", "user selected immediate for this request",
                           "--review-depth", "light", "--risk-reason", "small change",
                           "--work-item", "account-settings")
        run_dir = Path(result.stdout.strip()).resolve()
        (self.backend / "auth.py").write_text("changed\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.backend), "add", "auth.py"], check=True)
        subprocess.run(["git", "-C", str(self.backend), "-c", "user.name=Test",
                        "-c", "user.email=test@example.test", "commit", "-qm", "auth change"],
                       check=True)
        with self.assertRaisesRegex(ValueError, "high-risk path"):
            risk_guard(read_state(run_dir))

    def test_new_run_is_explicit_and_does_not_replace_prior_evidence(self) -> None:
        first = self.initialize()
        (first / "backend-design.md").write_text("retained\n", encoding="utf-8")
        second = self.initialize("--new-run")
        self.assertNotEqual(first, second)
        self.assertEqual((first / "backend-design.md").read_text(encoding="utf-8"),
                         "retained\n")
        self.call(HARNESS, "init", "--scope", "both",
                  "--frontend-repo", str(self.frontend),
                  "--backend-repo", str(self.backend),
                  "--review-mode", "immediate",
                  "--mode-reference", "user selected immediate for this request",
                  "--review-depth", "full", "--work-item", "account-settings",
                  success=False)

    def test_status_reports_next_stage_and_blocker_as_json(self) -> None:
        run_dir = self.initialize()
        result = self.call(HARNESS, "status", "--run-dir", str(run_dir))
        status = json.loads(result.stdout)
        self.assertTrue(status["stage"])
        self.assertNotEqual(status["next_action"], "complete")
        self.assertTrue(status["blocker"])
        self.assertFalse(status["complete"])

    def test_status_waits_for_unapproved_design_finding(self) -> None:
        run_dir = self.initialize()
        state_file = run_dir / "state.json"
        state = json.loads(state_file.read_text(encoding="utf-8"))
        state["events"].append({"type": "review", "area": "backend",
                                "result": "changes-required", "digest": "prior-design"})
        state_file.write_text(json.dumps(state), encoding="utf-8")
        status = json.loads(self.call(HARNESS, "status", "--run-dir", str(run_dir)).stdout)
        self.assertEqual(status["next_action"], "wait-user")
        self.assertFalse(status["complete"])

    def test_integration_digest_responds_to_either_repository(self) -> None:
        run_dir = self.initialize()
        state = read_state(run_dir)
        initial_integration = code_digest_for(run_dir, state, "integration")
        initial_backend = code_digest_for(run_dir, state, "backend")
        (self.frontend / "app.txt").write_text("frontend changed\n", encoding="utf-8")
        self.assertNotEqual(code_digest_for(run_dir, state, "integration"),
                            initial_integration)
        self.assertEqual(code_digest_for(run_dir, state, "backend"), initial_backend)

    def test_integration_digest_responds_to_independent_checkout(self) -> None:
        integration = self.make_repo("integration")
        run_dir = self.initialize("--integration-repo", str(integration))
        state = read_state(run_dir)
        initial = code_digest_for(run_dir, state, "integration")
        (integration / "app.txt").write_text("contract changed\n", encoding="utf-8")
        self.assertNotEqual(code_digest_for(run_dir, state, "integration"), initial)
        self.assertEqual(Path(state["repo_roots"]["integration"]), integration)

    def test_linked_worktrees_have_distinct_checkout_identity(self) -> None:
        linked = self.home / "linked-frontend"
        subprocess.run(["git", "-C", str(self.frontend), "worktree", "add", "--detach",
                        str(linked), "HEAD"], check=True, capture_output=True)
        self.assertNotEqual(checkout_identity(self.frontend), checkout_identity(linked))

    def test_dirty_submodule_content_changes_v7_digest(self) -> None:
        dependency = self.make_repo("dependency")
        subprocess.run(["git", "-c", "protocol.file.allow=always", "-C", str(self.backend),
                        "submodule", "add", "-q", str(dependency), "dependency"],
                       check=True, capture_output=True)
        run_dir = self.initialize()
        state = read_state(run_dir)
        nested = self.backend / "dependency" / "app.txt"
        nested.write_text("first change\n", encoding="utf-8")
        first = code_digest_for(run_dir, state, "backend")
        nested.write_text("second change\n", encoding="utf-8")
        self.assertNotEqual(code_digest_for(run_dir, state, "backend"), first)

    def test_light_review_rejects_dirty_submodule(self) -> None:
        dependency = self.make_repo("dependency")
        (dependency / "auth.py").write_text("original\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(dependency), "add", "auth.py"], check=True)
        subprocess.run(["git", "-C", str(dependency), "-c", "user.name=Test",
                        "-c", "user.email=test@example.test", "commit", "-qm", "auth"],
                       check=True)
        subprocess.run(["git", "-c", "protocol.file.allow=always", "-C", str(self.backend),
                        "submodule", "add", "-q", str(dependency), "dependency"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.backend), "-c", "core.hooksPath=/dev/null",
                        "-c", "user.name=Test", "-c", "user.email=test@example.test",
                        "commit", "-qm", "submodule baseline"], check=True)
        (self.backend / "dependency" / "auth.py").write_text("changed\n", encoding="utf-8")
        result = self.call(HARNESS, "init", "--scope", "backend", "--repo", str(self.backend),
                           "--review-mode", "immediate", "--mode-reference", "answer",
                           "--review-depth", "light", "--risk-reason", "small change",
                           "--work-item", "auth-check", success=False)
        self.assertIn("changed submodule", result.stderr)

    def test_light_review_rejects_removed_submodule(self) -> None:
        dependency = self.make_repo("dependency")
        subprocess.run(["git", "-c", "protocol.file.allow=always", "-C", str(self.backend),
                        "submodule", "add", "-q", str(dependency), "dependency"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.backend), "-c", "core.hooksPath=/dev/null",
                        "-c", "user.name=Test", "-c", "user.email=test@example.test",
                        "commit", "-qm", "submodule baseline"], check=True)
        subprocess.run(["git", "-C", str(self.backend), "rm", "--cached", "dependency"],
                       check=True, capture_output=True)
        result = self.call(HARNESS, "init", "--scope", "backend", "--repo", str(self.backend),
                           "--review-mode", "immediate", "--mode-reference", "answer",
                           "--review-depth", "light", "--risk-reason", "small change",
                           "--work-item", "submodule-removal", success=False)
        self.assertIn("changed submodule", result.stderr)

    def test_light_review_rejects_security_paths_and_gitmodules(self) -> None:
        for name in ("src/oauth.py", "src/password_reset.py", ".gitmodules"):
            path = self.backend / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("original\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(self.backend), "add", name], check=True)
        subprocess.run(["git", "-C", str(self.backend), "-c", "core.hooksPath=/dev/null",
                        "-c", "user.name=Test", "-c", "user.email=test@example.test",
                        "commit", "-qm", "risk paths"], check=True)
        for name in ("src/oauth.py", "src/password_reset.py", ".gitmodules"):
            path = self.backend / name
            path.write_text("changed\n", encoding="utf-8")
            result = self.call(HARNESS, "init", "--scope", "backend", "--repo", str(self.backend),
                               "--review-mode", "immediate", "--mode-reference", "answer",
                               "--review-depth", "light", "--risk-reason", "small change",
                               "--work-item", f"risk-{path.name}", success=False)
            self.assertIn("high-risk path", result.stderr)
            path.write_text("original\n", encoding="utf-8")

    def test_unborn_checkout_allows_low_risk_light_run(self) -> None:
        fresh = self.home / "fresh"
        fresh.mkdir()
        subprocess.run(["git", "-C", str(fresh), "init", "-q"], check=True)
        (fresh / "note.txt").write_text("initial\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(fresh), "add", "note.txt"], check=True)
        result = self.call(HARNESS, "init", "--scope", "backend", "--repo", str(fresh),
                           "--review-mode", "immediate", "--mode-reference", "answer",
                           "--review-depth", "light", "--risk-reason", "small local note",
                           "--work-item", "fresh-note")
        run_dir = Path(result.stdout.strip())
        self.assertTrue(run_dir.exists())
        base = read_state(run_dir)["base_commits"]["backend"]
        subprocess.run(["git", "-C", str(fresh), "-c", "user.name=Test",
                        "-c", "user.email=test@example.test", "commit", "-qm", "first"],
                       check=True)
        self.assertEqual(changed_paths(fresh, base), ["note.txt"])

    def test_one_approval_covers_all_current_design_findings(self) -> None:
        run_dir = self.initialize()
        self.write_design_and_plans(run_dir)
        state_file = run_dir / "state.json"
        state = json.loads(state_file.read_text(encoding="utf-8"))
        from workflow_harness import design_digest
        digest = design_digest(run_dir, "backend")
        state["events"] = [{"type": "review", "area": "backend", "slot": slot,
                            "agent_id": f"/root/reviewer-{slot}",
                            "result": "changes-required" if slot < 3 else "clear",
                            "digest": digest} for slot in (1, 2, 3)]
        state_file.write_text(json.dumps(state), encoding="utf-8")
        self.call(HARNESS, "approve", "--run-dir", str(run_dir),
                  "--area", "backend", "--reference", "user approved both findings")
        updated = json.loads(state_file.read_text(encoding="utf-8"))
        self.assertEqual(updated["events"][-1]["finding_indexes"], [0, 1])
        status = json.loads(self.call(HARNESS, "status", "--run-dir", str(run_dir)).stdout)
        self.assertNotEqual(status["next_action"], "wait-user")

    def test_runner_rejects_wrong_checkout_for_each_area(self) -> None:
        run_dir = self.initialize()
        self.write_design_and_plans(run_dir)
        self.call(RUN_CHECK, "--run-dir", str(run_dir), "--repo", str(self.frontend),
                  "--area", "backend", "--command", "true", success=False)
        self.call(RUN_CHECK, "--run-dir", str(run_dir), "--repo", str(self.backend),
                  "--area", "frontend", "--command", "true", success=False)
        self.call(RUN_CHECK, "--run-dir", str(run_dir), "--repo", str(self.frontend),
                  "--area", "integration", "--command", "true", success=False)
        result = self.call(RUN_CHECK, "--run-dir", str(run_dir),
                           "--repo", str(self.frontend), "--area", "frontend",
                           "--command", "true")
        manifest = json.loads(Path(result.stdout.strip()).read_text(encoding="utf-8"))
        self.assertEqual(manifest["area"], "frontend")
        self.assertEqual(Path(manifest["repo_root"]), self.frontend)
        integration = self.call(RUN_CHECK, "--run-dir", str(run_dir),
                                "--repo", str(self.backend), "--area", "integration",
                                "--command", "true")
        integration_manifest = json.loads(Path(integration.stdout.strip()).read_text(encoding="utf-8"))
        self.assertEqual(integration_manifest["area"], "integration")
        self.assertEqual(Path(integration_manifest["repo_root"]), self.backend)

    def test_concurrent_init_reuses_one_run(self) -> None:
        command = [sys.executable, str(HARNESS), "init", "--scope", "both",
                   "--frontend-repo", str(self.frontend), "--backend-repo", str(self.backend),
                   "--review-mode", "immediate", "--mode-reference", "answer",
                   "--review-depth", "full", "--work-item", "parallel"]
        processes = [subprocess.Popen(command, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, text=True,
                                      env=self.environment) for _ in range(2)]
        results = [process.communicate() for process in processes]
        self.assertTrue(all(process.returncode == 0 for process in processes), results)
        self.assertEqual(results[0][0].strip(), results[1][0].strip())

    def test_replaced_checkout_rejects_existing_run(self) -> None:
        run_dir = self.initialize()
        shutil.rmtree(self.frontend / ".git")
        subprocess.run(["git", "-C", str(self.frontend), "init", "-q"], check=True)
        result = self.call(HARNESS, "status", "--run-dir", str(run_dir), success=False)
        self.assertIn("Git checkout changed", result.stderr)
        resumed = self.call(HARNESS, "init", "--scope", "both",
                            "--frontend-repo", str(self.frontend),
                            "--backend-repo", str(self.backend),
                            "--review-mode", "immediate", "--mode-reference", "answer",
                            "--review-depth", "full", "--work-item", "account-settings",
                            success=False)
        self.assertIn("matching work item cannot be resumed", resumed.stderr)


if __name__ == "__main__":
    unittest.main()
