"""Verify the one-reviewer path and conservative escalation in v6 runs."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from orchestrate import build_plan, focus_for, model_for  # noqa: E402
from run_check import capture  # noqa: E402
from usage_checkpoint import record  # noqa: E402
from workflow_harness import plans, read_state, required_slots  # noqa: E402


class ReviewDepthV6Test(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        previous = os.environ.get("HOME")
        os.environ["HOME"] = str(self.home)
        self.addCleanup(lambda: os.environ.__setitem__("HOME", previous)
                        if previous is not None else os.environ.pop("HOME", None))
        self.repo = self.home / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "-C", str(self.repo), "init", "-q"], check=True)
        (self.repo / "app.py").write_text("value = 1\n")
        subprocess.run(["git", "-C", str(self.repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.repo), "-c", "core.hooksPath=/dev/null",
                        "-c", "user.name=Test", "-c", "user.email=test@example.test",
                        "commit", "-qm", "initial"], check=True)
        self.run_dir = self.home / "Documents" / "docs" / "run"

    def call(self, command: str, *args: str, expected: int = 0) -> str:
        result = subprocess.run([sys.executable, str(ROOT / "scripts" / "workflow_harness.py"),
                                 command, "--run-dir", str(self.run_dir), *args],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result.stdout + result.stderr

    def init(self, depth: str = "light") -> None:
        self.call("init", "--repo", str(self.repo), "--scope", "backend",
                  "--review-mode", "immediate", "--mode-reference", "this request",
                  "--review-depth", depth, "--risk-reason", "localized app logic")

    def result(self, stage: str, outcome: str) -> Path:
        data = {"schema_version": 1, "stage": stage, "area": "backend", "slot": 1,
                "agent_id": f"/root/{stage}", "focus": "comprehensive", "result": outcome,
                "findings": [], "evidence": []}
        path = self.run_dir / f"{stage}.json"
        path.write_text(json.dumps(data))
        rendered = subprocess.run([sys.executable, str(ROOT / "scripts" / "render_result.py"),
                                   "--run-dir", str(self.run_dir), "--input-json", str(path)],
                                  capture_output=True, text=True)
        self.assertEqual(rendered.returncode, 0, rendered.stderr)
        rendered = subprocess.run([sys.executable, str(ROOT / "scripts" / "render_result.py"),
                                   "--run-dir", str(self.run_dir), "--input-json", str(path),
                                   "--aggregate"], capture_output=True, text=True)
        self.assertEqual(rendered.returncode, 0, rendered.stderr)
        return path

    def record_agent(self, stage: str, outcome: str) -> None:
        path = self.result(stage, outcome)
        command = "review" if stage == "design-review" else "agent-result"
        args = ["--area", "backend", "--slot", "1", "--agent-id", f"/root/{stage}",
                "--focus", "comprehensive", "--result", outcome, "--result-json", str(path),
                "--model", "gpt-6-luna", "--effort", "medium", "--model-reason", "recommended",
                "--context-mode", "isolated", "--context-reason", "short brief"]
        if command == "agent-result":
            args += ["--stage", stage]
        self.call(command, *args)

    def write_plans(self) -> None:
        (self.run_dir / "backend-design.md").write_text("## 수용 기준\nR1. expected\n")
        (self.run_dir / "verification-plan.json").write_text(json.dumps({"backend": [
            {"id": "test", "kind": "test", "command": "true", "required": True}]}))
        kinds = ("normal", "boundary", "authorization", "regression", "operations")
        (self.run_dir / "qa-plan.json").write_text(json.dumps({"backend": [
            {"slot": 1, "scenario_id": kind, "criterion_id": "R1", "kind": kind,
             "steps": "run", "expected": "pass"} for kind in kinds]}))

    def test_light_final_gate_and_risk_escalation(self) -> None:
        self.init()
        self.write_plans()
        state = read_state(self.run_dir)
        self.assertEqual(required_slots(state), (1,))
        self.record_agent("design-review", "clear")
        self.call("check", "--gate", "design")
        manifest = capture(self.run_dir, self.repo, "true", area="backend")
        self.call("check-record", "--area", "backend", "--name", "test",
                  "--status", "pass", "--manifest-file", str(manifest))
        self.record_agent("code-review", "clear")
        for kind in ("normal", "boundary", "authorization", "regression", "operations"):
            evidence = self.run_dir / f"qa-{kind}.txt"
            evidence.write_text(f"{kind}: passed\n")
            self.call("qa-case", "--area", "backend", "--slot", "1", "--agent-id", "/root/qa",
                      "--scenario-id", kind, "--result", "pass", "--environment", "local",
                      "--input", kind, "--actual", "passed", "--evidence-file", str(evidence))
        self.record_agent("qa", "pass")
        (self.run_dir / "final-report.md").write_text("Observed pass; account usage not available.\n")
        self.call("check", "--gate", "final")
        (self.repo / "auth.py").write_text("changed\n")
        self.assertIn("high-risk path", self.call("check", "--gate", "final", expected=1))

    def test_light_plan_rejects_missing_kind_and_extra_slot(self) -> None:
        self.init()
        self.write_plans()
        state = read_state(self.run_dir)
        data = json.loads((self.run_dir / "qa-plan.json").read_text())
        data["backend"] = data["backend"][:-1]
        (self.run_dir / "qa-plan.json").write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "five scenario kinds"):
            plans(self.run_dir, state)
        data["backend"][-1]["slot"] = 2
        (self.run_dir / "qa-plan.json").write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            plans(self.run_dir, state)
        self.assertIn("outside the review depth", self.call(
            "review", "--area", "backend", "--slot", "2", "--agent-id", "/root/extra",
            "--focus", "architecture", "--result", "clear", expected=1))

    def test_full_and_legacy_stay_three_and_models_use_luna(self) -> None:
        self.assertEqual(len([task for task in build_plan("backend", review_depth="light")
                              if task["id"].startswith("review-code-")]), 1)
        self.assertEqual(len([task for task in build_plan("backend", review_depth="full")
                              if task["id"].startswith("review-code-")]), 3)
        with self.assertRaises(ValueError):
            build_plan("both", review_depth="light")
        self.assertEqual(focus_for("qa", "backend", 1, "light"), "comprehensive")
        for stage in ("design-review", "code-review", "qa"):
            for slot in (1, 2, 3):
                self.assertEqual(model_for(stage, slot)["recommended_model"], "gpt-6-luna")
        self.init("full")
        state = read_state(self.run_dir)
        self.assertEqual(required_slots(state), (1, 2, 3))
        state["version"] = 5
        (self.run_dir / "state.json").write_text(json.dumps(state))
        self.assertEqual(required_slots(read_state(self.run_dir)), (1, 2, 3))

    def test_risky_path_and_both_scope_rejected(self) -> None:
        (self.repo / "security.py").write_text("changed\n")
        self.assertIn("high-risk path", self.call(
            "init", "--repo", str(self.repo), "--scope", "backend",
            "--review-mode", "immediate", "--mode-reference", "this request",
            "--review-depth", "light", "--risk-reason", "small", expected=1))
        self.assertIn("one area", self.call(
            "init", "--repo", str(self.repo), "--scope", "both",
            "--review-mode", "immediate", "--mode-reference", "this request",
            "--review-depth", "light", "--risk-reason", "small", expected=1))

    def test_usage_checkpoint_reports_resolution_and_shared_window(self) -> None:
        self.init()
        self.assertEqual(record(self.run_dir, "start", 57)["status"], "below-display-resolution")
        self.assertEqual(record(self.run_dir, "design", 57)["status"], "below-display-resolution")
        observed = record(self.run_dir, "review", 58)
        self.assertEqual(observed["status"], "stop-next-optional-call")
        self.assertEqual(observed["visible_delta_points"], 1)
        self.assertIn("cannot prove", observed["limit"])
        self.assertEqual(record(self.run_dir, "missing", None)["status"], "unverified")
        self.assertEqual(record(self.run_dir, "reset", 1)["status"], "unverified")
