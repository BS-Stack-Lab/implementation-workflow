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
from orchestrate import (build_plan, exact_ownership_disjoint, focus_for, focus_question, model_for,
                         parallel_waves)  # noqa: E402
from run_check import capture  # noqa: E402
from usage_checkpoint import record  # noqa: E402
from workflow_harness import (compare_timing_summaries, plans, read_state, required_slots,
                              risk_guard, timing_summary_data)  # noqa: E402


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
                  "--review-depth", depth, "--risk-level", "low",
                  "--risk-reason", "localized app logic")
        state_file = self.run_dir / "state.json"
        state = json.loads(state_file.read_text(encoding="utf-8"))
        state["version"] = 7
        state_file.write_text(json.dumps(state), encoding="utf-8")
        (self.run_dir / "official-sources.json").write_text(json.dumps({
            "schema_version": 1, "status": "not_applicable", "sources": [],
            "reason": "isolated harness fixture for review-depth compatibility"}))

    def result(self, stage: str, outcome: str, risk_level: str = "low") -> Path:
        data = {"schema_version": 1, "stage": stage, "area": "backend", "slot": 1,
                "agent_id": f"/root/{stage}", "focus": "comprehensive", "result": outcome,
                "findings": [], "evidence": []}
        if stage in {"design-review", "code-review"}:
            data["risk_level"] = risk_level
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

    def record_agent(self, stage: str, outcome: str, risk_level: str = "low",
                     expected: int = 0) -> None:
        path = self.result(stage, outcome, risk_level)
        command = "review" if stage == "design-review" else "agent-result"
        args = ["--area", "backend", "--slot", "1", "--agent-id", f"/root/{stage}",
                "--focus", "comprehensive", "--result", outcome, "--result-json", str(path),
                "--model", "gpt-6-luna", "--effort", "medium", "--model-reason", "recommended",
                "--context-mode", "isolated", "--context-reason", "short brief"]
        if command == "agent-result":
            args += ["--stage", stage]
            if stage == "code-review":
                args += ["--risk-level", risk_level]
        else:
            args += ["--risk-level", risk_level]
        self.call(command, *args, expected=expected)

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
            "--review-depth", "light", "--risk-level", "low",
            "--risk-reason", "small", expected=1))
        self.assertIn("one area", self.call(
            "init", "--repo", str(self.repo), "--scope", "both",
            "--review-mode", "immediate", "--mode-reference", "this request",
            "--review-depth", "light", "--risk-level", "low",
            "--risk-reason", "small", expected=1))

    def test_light_profile_rejects_github_shared_path(self) -> None:
        self.init("light")
        github_workflow = self.repo / ".github" / "workflows" / "ci.yml"
        github_workflow.parent.mkdir(parents=True)
        github_workflow.write_text("name: changed\n")
        with self.assertRaisesRegex(ValueError, "high-risk path or shared path"):
            risk_guard(read_state(self.run_dir), self.run_dir)

    def test_medium_reviewer_assessment_escalates_light_profile(self) -> None:
        state = {"policy_version": 3, "version": 8, "risk_level": "low",
                 "risk_reason": "localized change", "scope": "backend",
                 "review_depth": "light", "events": [
                     {"type": "review", "area": "backend", "risk_level": "medium"}]}
        with self.assertRaisesRegex(ValueError, "new full run"):
            risk_guard(state)

    def test_high_code_review_risk_is_recorded_and_blocks_qa(self) -> None:
        self.init()
        self.write_plans()
        self.record_agent("design-review", "clear")
        self.call("check", "--gate", "design")
        manifest = capture(self.run_dir, self.repo, "true", area="backend")
        self.call("check-record", "--area", "backend", "--name", "test",
                  "--status", "pass", "--manifest-file", str(manifest))
        self.record_agent("code-review", "clear", risk_level="high", expected=1)
        state = read_state(self.run_dir)
        self.assertEqual(state["events"][-1]["risk_level"], "high")
        self.assertIn("new full run", self.call(
            "qa-case", "--area", "backend", "--slot", "1", "--agent-id", "/root/qa",
            "--scenario-id", "normal", "--result", "pass", "--environment", "local",
            "--input", "valid input", "--actual", "expected output", expected=1))
        qa_result = self.result("qa", "pass")
        self.assertIn("new full run", self.call(
            "agent-result", "--stage", "qa", "--area", "backend", "--slot", "1",
            "--agent-id", "/root/qa", "--focus", "comprehensive", "--result", "pass",
            "--result-json", str(qa_result), "--model", "gpt-6-luna", "--effort", "medium",
            "--model-reason", "recommended", "--context-mode", "isolated",
            "--context-reason", "short brief", expected=1))

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

    def test_parallel_waves_validate_graph_and_are_deterministic(self) -> None:
        graph = [
            {"id": "b", "depends_on": ["a"]},
            {"id": "c", "depends_on": []},
            {"id": "a", "depends_on": []},
        ]
        expected = [["a", "c"], ["b"]]
        self.assertEqual(parallel_waves(graph), expected)
        self.assertEqual(parallel_waves(graph), expected)
        for invalid in (
            [{"id": "a", "depends_on": []}, {"id": "a", "depends_on": []}],
            [{"id": "a", "depends_on": ["missing"]}],
            [{"id": "a", "depends_on": ["a"]}],
            [{"id": "a", "depends_on": ["b"]}, {"id": "b", "depends_on": ["a"]}],
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                parallel_waves(invalid)

    def test_balanced_and_same_checkout_plans_keep_gate_order(self) -> None:
        balanced = build_plan("both", review_depth="balanced")
        self.assertEqual(len([task for task in balanced if task["id"].startswith("qa-")]), 3)
        self.assertEqual(focus_question("qa", "frontend", 1, "balanced"),
                         "정상·경계·권한·회귀·운영 시나리오의 실제 증거가 있는가?")
        same = build_plan("both", review_depth="balanced", same_checkout=True)
        by_id = {task["id"]: task for task in same}
        self.assertIn("test-frontend", by_id["implement-backend"]["depends_on"])
        self.assertIn("review-code-frontend-1", by_id["implement-backend"]["depends_on"])
        self.assertEqual(by_id["review-code-frontend-1"]["depends_on"], ["test-frontend"])
        self.assertIn("review-code-frontend-1", by_id["review-code-backend-1"]["depends_on"])
        waves = parallel_waves(same)
        self.assertLess(next(i for i, wave in enumerate(waves) if "test-frontend" in wave),
                        next(i for i, wave in enumerate(waves) if "implement-backend" in wave))
        self.assertLess(next(i for i, wave in enumerate(waves)
                             if "review-code-frontend-1" in wave),
                        next(i for i, wave in enumerate(waves)
                             if "review-code-backend-1" in wave))
        self.assertLess(next(i for i, wave in enumerate(waves)
                             if "review-code-frontend-1" in wave),
                        next(i for i, wave in enumerate(waves)
                             if "implement-backend" in wave))
        cli = subprocess.run([sys.executable, str(ROOT / "scripts" / "orchestrate.py"),
                              "--scope", "both", "--review-mode", "immediate",
                              "--review-depth", "balanced"], capture_output=True, text=True)
        self.assertEqual(cli.returncode, 0, cli.stderr)
        self.assertEqual(len(json.loads(cli.stdout)), len(balanced))

    def test_timing_attempts_require_matched_start_and_finish(self) -> None:
        self.init()
        self.write_plans()
        log_path = self.run_dir / "timing.log"
        log_path.write_text("verified command output\n")
        started = json.loads(self.call("timing-start", "--task-id", "implement-backend",
                                       "--wave-id", "wave-1",
                                       "--wave-execution-id", "execution-1",
                                       "--fixture-id", "fixture-small",
                                       "--environment-id", "python3.14-local"))
        (self.repo / "app.py").write_text("value = 2\n")
        self.call("timing-finish", "--attempt-id", started["attempt_id"],
                  "--log-file", str(log_path))
        summary = json.loads(self.call("timing-summary"))
        self.assertEqual(len(summary["completed_attempts"]), 1)
        attempt = summary["completed_attempts"][0]
        self.assertEqual(attempt["task_id"], "implement-backend")
        self.assertNotEqual(attempt["start_code_binding"], attempt["finish_code_binding"])
        self.assertTrue(attempt["log_digest"])
        self.assertEqual(attempt["fixture_id"], "fixture-small")
        self.assertTrue(attempt["started_at"])
        self.assertTrue(attempt["finished_at"])
        self.assertEqual(summary["improvement_status"], "unverified")
        self.assertEqual(summary["waves"][0]["wave_id"], "wave-1")
        self.assertIn("already finished", self.call(
            "timing-finish", "--attempt-id", started["attempt_id"],
            "--log-file", str(log_path), expected=1))

    def test_timing_execution_ids_bind_fixture_environment_and_unique_tasks(self) -> None:
        self.init()
        self.write_plans()
        first = json.loads(self.call("timing-start", "--task-id", "implement-backend",
                                     "--wave-id", "wave-1",
                                     "--wave-execution-id", "execution-1",
                                     "--fixture-id", "fixture-small",
                                     "--environment-id", "python3.14-local"))
        self.assertIn("already has a timing attempt", self.call(
            "timing-start", "--task-id", "implement-backend", "--wave-id", "wave-1",
            "--wave-execution-id", "execution-1", "--fixture-id", "fixture-small",
            "--environment-id", "python3.14-local", expected=1))
        self.assertIn("already bound", self.call(
            "timing-start", "--task-id", "implement-frontend", "--wave-id", "wave-1",
            "--wave-execution-id", "execution-1", "--fixture-id", "fixture-large",
            "--environment-id", "python3.14-local", expected=1))
        self.assertTrue(first["attempt_id"])

    def test_timing_wave_summary_keeps_fixture_and_environment_separate(self) -> None:
        state = {"run_id": "test-run", "events": []}
        for suffix, fixture, environment, start_ns in (
                ("a", "fixture-small", "local", 10),
                ("b", "fixture-large", "local", 20),
                ("c", "fixture-small", "container", 30),
                ("d", "fixture-small", "local", 40)):
            start_id = "s" + suffix
            attempt_id = "a" + suffix
            state["events"].extend([
                {"type": "timing-start", "attempt_id": attempt_id, "event_id": start_id,
                 "task_id": suffix, "wave_id": "wave-1", "wave_execution_id": "exec-1",
                 "session_id": "boot", "fixture_id": fixture, "environment_id": environment,
                 "started_monotonic_ns": start_ns, "plan_digests": {"plan": "same"},
                 "code_binding": {"backend": "second" if suffix == "d" else "first"}},
                {"type": "timing-finish", "attempt_id": attempt_id,
                 "start_event_id": start_id, "session_id": "boot",
                 "finished_monotonic_ns": start_ns + 100,
                 "plan_digests": {"plan": "same"}, "code_binding": {}}])
        summary = timing_summary_data(state)
        self.assertEqual(len(summary["waves"]), 4)
        self.assertEqual({(item["fixture_id"], item["environment_id"])
                          for item in summary["waves"]},
                         {("fixture-small", "local"), ("fixture-large", "local"),
                          ("fixture-small", "container")})
        self.assertEqual(len({item["start_code_binding_digest"] for item in summary["waves"]}), 2)

    def test_timing_compare_requires_quality_parity_and_three_matched_samples(self) -> None:
        quality = {"status": "pass", "scope": "backend", "review_mode": "immediate",
                   "review_depth": "full",
                   "policy_version": 3, "work_item": "sample",
                   "repo_roots": {"backend": "/repo"}, "checkout_ids": {"backend": "checkout"},
                   "plan_contract": {"verification-plan.json": "v", "qa-plan.json": "q"},
                   "required_check_count": 2, "qa_scenario_count": 5, "review_slots": [1, 2, 3],
                   "evidence_digest": "quality-evidence"}
        plan = {"verification-plan.json": "v", "qa-plan.json": "q"}

        def make_attempts(durations: list[float], code_digest: str) -> list[dict]:
            return [{"task_id": "implement-backend", "wave_id": "wave-1",
                     "fixture_id": "sample-fixture", "environment_id": "python3.14-local",
                     "plan_digests": plan, "start_code_binding": {"backend": code_digest},
                     "duration_ms": duration} for duration in durations]

        baseline = {"run_id": "before", "quality": quality,
                    "completed_attempts": make_attempts([100, 110, 90], "before-code")}
        candidate = {"run_id": "after", "quality": {**quality, "evidence_digest": "after-evidence"},
                     "completed_attempts": make_attempts([80, 85, 75], "after-code")}
        result = compare_timing_summaries(baseline, candidate)
        self.assertEqual(result["improvement_status"], "improved")
        self.assertEqual(result["quality_status"], "equivalent")
        self.assertEqual(result["comparisons"][0]["baseline_median_ms"], 100)
        self.assertEqual(result["comparisons"][0]["candidate_median_ms"], 80)
        candidate["completed_attempts"] = make_attempts([80, 75], "after-code")
        self.assertEqual(compare_timing_summaries(baseline, candidate)["improvement_status"],
                         "unverified")
        candidate["quality"] = {**quality, "plan_contract": {"verification-plan.json": "changed"}}
        self.assertEqual(compare_timing_summaries(baseline, candidate)["improvement_status"],
                         "unverified")
        candidate["quality"] = {**quality, "review_mode": "user-review"}
        self.assertEqual(compare_timing_summaries(baseline, candidate)["improvement_status"],
                         "unverified")
        candidate["quality"] = {**quality, "evidence_digest": "after-evidence"}
        baseline["completed_attempts"] = make_attempts([100, 110, 90], "before-code")
        baseline["completed_attempts"].extend(
            {**attempt, "fixture_id": "second-fixture"}
            for attempt in make_attempts([100, 110, 90], "before-code")
        )
        candidate["completed_attempts"] = make_attempts([80, 85, 75], "after-code")
        self.assertEqual(compare_timing_summaries(baseline, candidate)["improvement_status"],
                         "unverified")
        candidate["completed_attempts"].extend(
            {**attempt, "fixture_id": "second-fixture"}
            for attempt in make_attempts([80, 85], "after-code")
        )
        self.assertEqual(compare_timing_summaries(baseline, candidate)["improvement_status"],
                         "unverified")

    def test_timing_summary_uses_parallel_wave_span_and_ignores_unmatched(self) -> None:
        state = {"run_id": "test-run", "events": [
            {"type": "timing-start", "attempt_id": "a", "event_id": "sa",
             "task_id": "a", "wave_id": "wave-1", "wave_execution_id": "exec-1", "session_id": "boot",
             "fixture_id": "fixture", "environment_id": "local",
             "started_monotonic_ns": 10, "plan_digests": {"plan": "same"}, "code_binding": {}},
            {"type": "timing-finish", "attempt_id": "a", "start_event_id": "sa",
             "session_id": "boot", "finished_monotonic_ns": 110,
             "plan_digests": {"plan": "same"}, "code_binding": {}},
            {"type": "timing-start", "attempt_id": "b", "event_id": "sb",
             "task_id": "b", "wave_id": "wave-1", "wave_execution_id": "exec-1", "session_id": "boot",
             "fixture_id": "fixture", "environment_id": "local",
             "started_monotonic_ns": 20, "plan_digests": {"plan": "same"}, "code_binding": {}},
            {"type": "timing-finish", "attempt_id": "b", "start_event_id": "sb",
             "session_id": "boot", "finished_monotonic_ns": 210,
             "plan_digests": {"plan": "same"}, "code_binding": {}},
            {"type": "timing-start", "attempt_id": "unfinished", "event_id": "su",
             "task_id": "c", "wave_id": "wave-1", "wave_execution_id": "exec-1", "session_id": "boot",
             "fixture_id": "fixture", "environment_id": "local",
             "started_monotonic_ns": 30, "plan_digests": {"plan": "same"}, "code_binding": {}}
        ]}
        summary = timing_summary_data(state)
        self.assertEqual(summary["waves"][0]["duration_ms"], 0.0002)
        self.assertEqual(summary["unmatched_starts"], ["unfinished"])

    def test_shared_ownership_path_disables_parallel_implementation(self) -> None:
        self.run_dir.mkdir(parents=True)
        mapping = {"exclusive_paths": {
            "frontend": [{"path": "ui/page.tsx", "reason": "frontend-owned"}],
            "backend": [{"path": "api/handler.py", "reason": "backend-owned"}],
            "shared": [{"path": "contracts/account.json", "reason": "shared contract"}]}}
        (self.run_dir / "impact-map.json").write_text(json.dumps(mapping))
        self.assertFalse(exact_ownership_disjoint(self.run_dir))
