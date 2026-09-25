"""Exercise v4 acceptance coverage, ordered gates, and immutable evidence."""

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
from orchestrate import build_plan, focus_for  # noqa: E402


class WorkflowHarnessV4Test(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        self.repo = self.home / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "-C", str(self.repo), "init", "-q"], check=True)
        (self.repo / "app.py").write_text("value = 1\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repo), "add", "app.py"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "-c", "core.hooksPath=/dev/null",
                        "-c", "user.name=Test", "-c", "user.email=test@example.test",
                        "commit", "-qm", "initial"], check=True)
        self.run_dir = self.home / "Documents" / "docs" / "run"

    def call(self, *args: str, fails: bool = False) -> subprocess.CompletedProcess[str]:
        result = subprocess.run([sys.executable, str(HARNESS), *args], text=True,
                                capture_output=True, env={**os.environ, "HOME": str(self.home)})
        if fails:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def command(self, name: str, *args: str, fails: bool = False) -> subprocess.CompletedProcess[str]:
        return self.call(name, "--run-dir", str(self.run_dir), *args, fails=fails)

    def write(self, name: str, content: str = "evidence\n") -> Path:
        path = self.run_dir / name
        path.write_text(content, encoding="utf-8")
        return path

    def initialize(self, *, criteria: tuple[str, ...] = ("R1", "R2", "R3")) -> None:
        self.call("init", "--repo", str(self.repo), "--scope", "frontend",
                  "--review-mode", "immediate", "--mode-reference", "answer for this request",
                  "--run-dir", str(self.run_dir))
        state = json.loads((self.run_dir / "state.json").read_text(encoding="utf-8"))
        # Exercise compatibility with runs created before v5 became the default.
        state["version"] = 4
        (self.run_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
        self.assertEqual(state["version"], 4)
        self.write("frontend-design.md", "# Design\n\n## 수용 기준\n\n" +
                   "\n".join(f"{criterion}. expected behavior" for criterion in criteria) + "\n")
        self.write("frontend-design-review.md")
        self.write("verification-plan.json", json.dumps({"frontend": [
            {"id": "tests", "kind": "test", "command": "python3 -m unittest", "required": True},
            {"id": "lint", "kind": "lint", "command": "python3 -m compileall scripts",
             "required": False, "reason": "not needed for this fixture"},
        ]}))
        self.write("qa-plan.json", json.dumps({"frontend": [
            {"slot": (slot - 1) % 3 + 1, "scenario_id": f"s{slot}", "criterion_id": criterion,
             "steps": f"exercise {criterion}", "expected": f"result {criterion}"}
            for slot, criterion in enumerate(criteria, 1)
        ]}))

    def review(self, slot: int, *, focus: str | None = None, fails: bool = False) -> None:
        self.write(f"frontend-design-review-{slot}.md")
        self.command("review", "--area", "frontend", "--slot", str(slot),
                     "--agent-id", f"/root/design-{slot}", "--result", "clear",
                     "--focus", focus or focus_for("design-review", "frontend", slot), fails=fails)

    def design_gate(self) -> None:
        for slot in (1, 2, 3):
            self.review(slot)
        self.command("check", "--gate", "design")

    def check(self, status: str, *, name: str = "tests", evidence: str | None = None,
              fails: bool = False) -> None:
        args = ["--area", "frontend", "--name", name, "--status", status]
        if status == "not-run":
            args += ["--reason", "tool unavailable", "--unverified", "static validation"]
        else:
            args += ["--actual", status, "--evidence-file", str(self.write(evidence or "check.txt"))]
        self.command("check-record", *args, fails=fails)

    def agent_result(self, stage: str, slot: int, *, result: str | None = None,
                     focus: str | None = None, fails: bool = False) -> None:
        result = result or ("clear" if stage == "code-review" else "pass")
        stem = "frontend-code-review" if stage == "code-review" else "frontend-qa"
        self.write(f"{stem}-{slot}.md")
        self.command("agent-result", "--stage", stage, "--area", "frontend", "--slot", str(slot),
                     "--agent-id", f"/root/{stage}-{slot}", "--result", result,
                     "--focus", focus or focus_for(stage, "frontend", slot), fails=fails)

    def reviewed_code(self) -> None:
        self.check("pass", evidence="tests-pass.txt")
        self.check("not-run", name="lint")
        for slot in (1, 2, 3):
            self.agent_result("code-review", slot)

    def qa_case(self, slot: int, result: str, *, evidence: str | None = None,
                fails: bool = False) -> None:
        args = ["--area", "frontend", "--slot", str(slot),
                "--agent-id", f"/root/qa-{slot}", "--scenario-id", f"s{slot}",
                "--result", result, "--environment", "local test repo",
                "--input", f"case {slot}", "--actual", result]
        if result == "not-run":
            args += ["--reason", "device unavailable"]
        else:
            args += ["--evidence-file", str(self.write(evidence or f"qa-{slot}.txt"))]
        self.command("qa-case", *args, fails=fails)

    def test_new_run_requires_distinct_focus_and_design_gate(self) -> None:
        self.initialize()
        plan = build_plan("frontend")
        for stage, prefix in (("design-review", "review-design-frontend-"),
                              ("code-review", "review-code-frontend-"),
                              ("qa", "qa-frontend-")):
            tasks = [task for task in plan if task["id"].startswith(prefix)]
            self.assertEqual(len(tasks), 3)
            self.assertEqual(len({task["focus"] for task in tasks}), 3)
            self.assertTrue(all(task["question"] for task in tasks))
        self.review(1, focus="wrong", fails=True)
        self.command("check-record", "--area", "frontend", "--name", "tests",
                     "--status", "not-run", "--reason", "not run", "--unverified", "tests", fails=True)
        self.design_gate()
        self.agent_result("code-review", 1, fails=True)

    def test_design_gate_catches_missing_or_unknown_criterion(self) -> None:
        self.initialize(criteria=("R1", "R2", "R3", "R4"))
        plan = json.loads((self.run_dir / "qa-plan.json").read_text(encoding="utf-8"))
        final_scenario = plan["frontend"].pop()
        self.write("qa-plan.json", json.dumps(plan))
        self.review(1, fails=True)
        plan["frontend"].append(final_scenario)
        plan["frontend"][-1]["criterion_id"] = "R9"
        self.write("qa-plan.json", json.dumps(plan))
        self.review(1, fails=True)
        plan["frontend"][-1]["criterion_id"] = "R4"
        self.write("qa-plan.json", json.dumps(plan))
        for slot in (1, 2, 3):
            self.review(slot)
        self.command("check", "--gate", "design")
        self.check("pass", evidence="first.txt")
        plan["frontend"][-1]["steps"] = "revised step"
        self.write("qa-plan.json", json.dumps(plan))
        self.command("check", "--gate", "design", fails=True)
        self.check("pass", evidence="second.txt", fails=True)

    def test_duplicate_design_criterion_is_rejected(self) -> None:
        self.initialize()
        design = (self.run_dir / "frontend-design.md").read_text(encoding="utf-8")
        self.write("frontend-design.md", design + "R2. duplicate\n")
        self.review(1, fails=True)

    def test_criterion_without_an_id_is_rejected(self) -> None:
        self.initialize()
        design = (self.run_dir / "frontend-design.md").read_text(encoding="utf-8")
        self.write("frontend-design.md", design + "- acceptance behavior without an ID\n")
        self.review(1, fails=True)

    def test_optional_failure_cannot_be_hidden_by_not_run(self) -> None:
        self.initialize()
        self.design_gate()
        self.check("pass", evidence="required-pass.txt")
        self.check("fail", name="lint", evidence="optional-fail.txt")
        self.check("not-run", name="lint")
        self.agent_result("code-review", 1, fails=True)

    def test_same_code_review_result_can_be_rerecorded_after_new_check(self) -> None:
        self.initialize()
        self.design_gate()
        self.check("pass", evidence="required-first.txt")
        self.check("not-run", name="lint")
        self.agent_result("code-review", 1)
        self.check("pass", evidence="required-second.txt")
        self.agent_result("code-review", 2)
        self.agent_result("code-review", 3)
        self.qa_case(1, "pass", fails=True)
        self.agent_result("code-review", 1)
        self.qa_case(1, "pass")

    def test_new_check_and_code_reviews_require_fresh_qa_before_final(self) -> None:
        self.initialize()
        self.design_gate()
        self.reviewed_code()
        for slot in (1, 2, 3):
            self.qa_case(slot, "pass", evidence=f"qa-first-{slot}.txt")
            self.agent_result("qa", slot)
        self.write("frontend-code-review.md")
        self.write("frontend-qa.md")
        self.write("final-report.md")
        self.command("check", "--gate", "final")

        self.check("pass", evidence="tests-rerun.txt")
        for slot in (1, 2, 3):
            self.agent_result("code-review", slot)
        self.command("check", "--gate", "final", fails=True)

        for slot in (1, 2, 3):
            self.qa_case(slot, "pass", evidence=f"qa-rerun-{slot}.txt")
            self.agent_result("qa", slot)
        self.command("check", "--gate", "final")

    def test_required_check_and_review_order(self) -> None:
        self.initialize()
        self.design_gate()
        self.agent_result("code-review", 1, fails=True)
        self.check("not-run")
        self.agent_result("code-review", 1, fails=True)
        self.check("pass", evidence="rerun.txt", fails=True)
        self.command("resolve-check", "--area", "frontend", "--name", "tests",
                     "--reference", "fixed test environment")
        self.check("pass", evidence="rerun.txt")
        self.check("not-run", name="lint")
        self.agent_result("code-review", 1, focus="wrong", fails=True)
        self.agent_result("code-review", 1)
        self.qa_case(1, "pass", fails=True)
        for slot in (2, 3):
            self.agent_result("code-review", slot)
        self.qa_case(1, "pass")

    def test_qa_recovery_requires_same_scenario_and_new_evidence(self) -> None:
        self.initialize()
        self.design_gate()
        self.reviewed_code()
        self.agent_result("qa", 1, result="fail", fails=True)
        self.qa_case(1, "fail", evidence="qa-failed.txt")
        self.agent_result("qa", 1, fails=True)
        self.qa_case(1, "pass", evidence="qa-rerun.txt", fails=True)
        self.command("resolve-qa-case", "--area", "frontend", "--slot", "2",
                     "--scenario-id", "s1", "--reference", "wrong slot", fails=True)
        self.command("resolve-qa-case", "--area", "frontend", "--slot", "1",
                     "--scenario-id", "s1", "--reference", "repaired selector")
        self.qa_case(1, "pass", evidence="qa-failed.txt", fails=True)
        self.qa_case(1, "pass", evidence="qa-rerun.txt")
        for slot in (2, 3):
            self.qa_case(slot, "pass")
        for slot in (1, 2, 3):
            self.agent_result("qa", slot)
        self.write("frontend-code-review.md")
        self.write("frontend-qa.md")
        self.write("final-report.md")
        self.command("check", "--gate", "final")
        self.write("qa-failed.txt", "tampered earlier attempt\n")
        self.command("check", "--gate", "final", fails=True)

    def test_evidence_must_be_local_regular_and_nonempty(self) -> None:
        self.initialize()
        self.design_gate()
        outside = self.repo / "output.txt"
        outside.write_text("outside\n", encoding="utf-8")
        self.check("pass", evidence="empty.txt", fails=False)
        self.command("check-record", "--area", "frontend", "--name", "tests",
                     "--status", "pass", "--actual", "pass", "--evidence-file", str(outside),
                     fails=True)
        self.write("empty-file.txt", "")
        self.command("check-record", "--area", "frontend", "--name", "tests",
                     "--status", "pass", "--actual", "pass",
                     "--evidence-file", str(self.run_dir / "empty-file.txt"), fails=True)
        (self.run_dir / "linked-output.txt").symlink_to(outside)
        self.command("check-record", "--area", "frontend", "--name", "tests",
                     "--status", "pass", "--actual", "pass",
                     "--evidence-file", str(self.run_dir / "linked-output.txt"), fails=True)

    def test_both_scope_integration_requires_its_check_and_both_area_qa(self) -> None:
        self.call("init", "--repo", str(self.repo), "--scope", "both",
                  "--review-mode", "immediate", "--mode-reference", "answer for this request",
                  "--run-dir", str(self.run_dir))
        state = json.loads((self.run_dir / "state.json").read_text(encoding="utf-8"))
        state["version"] = 4
        (self.run_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
        for area in ("frontend", "backend"):
            self.write(f"{area}-design.md", "# Design\n\n## 수용 기준\n\nR1. behavior\n")
            self.write(f"{area}-design-review.md")
        self.write("integration-contract-review.md")
        self.write("verification-plan.json", json.dumps({area: [
            {"id": "tests", "kind": "test", "command": "python3 -m unittest", "required": True}
        ] for area in ("frontend", "backend", "integration")}))
        self.write("qa-plan.json", json.dumps({area: [
            {"slot": slot, "scenario_id": f"{area}-s{slot}", "criterion_id": "R1",
             "source_area": "backend" if area == "integration" and slot == 3 else "frontend"
             if area == "integration" else area,
             "steps": "exercise behavior", "expected": "behavior works"}
            for slot in (1, 2, 3)
        ] for area in ("frontend", "backend", "integration")}))
        for area in ("frontend", "backend", "integration"):
            stem = "integration-contract-review" if area == "integration" else f"{area}-design-review"
            for slot in (1, 2, 3):
                self.write(f"{stem}-{slot}.md")
                self.command("review", "--area", area, "--slot", str(slot),
                             "--agent-id", f"/root/design-{area}-{slot}", "--result", "clear",
                             "--focus", focus_for("design-review", area, slot))
        self.command("check", "--gate", "design")

        def run_check(area: str) -> None:
            evidence = self.write(f"{area}-tests.txt")
            self.command("check-record", "--area", area, "--name", "tests",
                         "--status", "pass", "--actual", "passed", "--evidence-file", str(evidence))

        def qa_case(area: str, slot: int, *, fails: bool = False) -> None:
            evidence = self.write(f"{area}-qa-{slot}.txt")
            self.command("qa-case", "--area", area, "--slot", str(slot),
                         "--agent-id", f"/root/qa-{area}-{slot}",
                         "--scenario-id", f"{area}-s{slot}", "--result", "pass",
                         "--environment", "local test repo", "--input", "case",
                         "--actual", "passed", "--evidence-file", str(evidence), fails=fails)

        for area in ("frontend", "backend"):
            run_check(area)
            for slot in (1, 2, 3):
                self.write(f"{area}-code-review-{slot}.md")
                self.command("agent-result", "--stage", "code-review", "--area", area,
                             "--slot", str(slot), "--agent-id", f"/root/code-{area}-{slot}",
                             "--result", "clear", "--focus", focus_for("code-review", area, slot))
        qa_case("integration", 1, fails=True)
        run_check("integration")
        qa_case("integration", 1, fails=True)
        for area in ("frontend", "backend"):
            for slot in (1, 2, 3):
                qa_case(area, slot)
                self.write(f"{area}-qa-{slot}.md")
                self.command("agent-result", "--stage", "qa", "--area", area,
                             "--slot", str(slot), "--agent-id", f"/root/qa-{area}-{slot}",
                             "--result", "pass", "--focus", focus_for("qa", area, slot))
        qa_case("integration", 1)
        failure = self.write("frontend-failed-after-qa.txt")
        self.command("check-record", "--area", "frontend", "--name", "tests",
                     "--status", "fail", "--actual", "regression found",
                     "--evidence-file", str(failure))
        evidence = self.write("integration-qa-after-failure.txt")
        result = self.command("qa-case", "--area", "integration", "--slot", "2",
                              "--agent-id", "/root/qa-integration-2",
                              "--scenario-id", "integration-s2", "--result", "pass",
                              "--environment", "local test repo", "--input", "case",
                              "--actual", "passed", "--evidence-file", str(evidence), fails=True)
        self.assertIn("required verification is not passed", result.stderr)


if __name__ == "__main__":
    unittest.main()
