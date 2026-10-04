from __future__ import annotations

import json
import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from orchestrate import build_plan
from render_result import requires_test_code_assessment
from workflow_harness import (design_digest, plan_digests, plans, read_state,
                              risk_guard, validate_scope_contract, write_state)


class PolicyV4Test(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        previous_home = os.environ.get("HOME")
        os.environ["HOME"] = str(self.home)
        self.addCleanup(lambda: os.environ.__setitem__("HOME", previous_home)
                        if previous_home is not None else os.environ.pop("HOME", None))
        self.env = {**os.environ, "HOME": str(self.home), "GIT_CONFIG_GLOBAL": "/dev/null"}
        self.repo = self.home / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "-C", str(self.repo), "init", "-q"], check=True)
        (self.repo / "app.py").write_text("value = 1\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.repo), "-c", "core.hooksPath=/dev/null",
                        "-c", "user.name=Test", "-c", "user.email=test@example.test",
                        "commit", "-qm", "initial"], check=True)
        self.run_dir = self.home / "Documents" / "docs" / "run"

    def init(self, depth: str = "balanced", size: str = "medium", risk: str = "medium",
             expected: int = 0) -> None:
        args = [sys.executable, str(ROOT / "scripts" / "workflow_harness.py"), "init",
                "--repo", str(self.repo), "--scope", "backend", "--review-mode", "immediate",
                "--mode-reference", "user chose immediate", "--review-depth", depth,
                "--risk-level", risk, "--risk-reason", "multiple related branches",
                "--policy-version", "4", "--task-size", size,
                "--scope-goal", "Implement requested behavior", "--in-scope", "backend source and tests",
                "--out-of-scope", "unrequested refactors", "--completion-criterion",
                "required checks and QA pass", "--run-dir", str(self.run_dir)]
        result = subprocess.run(args, capture_output=True, text=True, env=self.env)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)

    def cli(self, command: str, *args: str, expected: int = 0) -> subprocess.CompletedProcess[str]:
        result = subprocess.run([sys.executable, str(ROOT / "scripts" / "workflow_harness.py"),
                                 command, "--run-dir", str(self.run_dir), *args],
                                capture_output=True, text=True, env=self.env)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def report_draft(self) -> Path:
        draft = self.run_dir / "report-draft.md"
        draft.write_text("## 변경 파일 및 코드 위치\n변경 없음\n\n"
                         "## 동작 원리\n하네스가 정책 상태를 기록한다\n\n"
                         "## 검증 결과\n테스트 실행 전 보고 경로 확인\n\n"
                         "## 요청 밖 문제 및 질문\n추가 작업 여부 질문 기록\n", encoding="utf-8")
        return draft

    def prepare_report_binding(self) -> None:
        (self.run_dir / "backend-design.md").write_text("## 설계\n정책 실행\n", encoding="utf-8")
        (self.run_dir / "verification-plan.json").write_text("{}\n", encoding="utf-8")
        (self.run_dir / "qa-plan.json").write_text("{}\n", encoding="utf-8")
        (self.run_dir / "official-sources.json").write_text(json.dumps({
            "schema_version": 1, "status": "not_applicable", "sources": [],
            "reason": "report binding test"}), encoding="utf-8")

    def test_v4_init_persists_contract_and_binds_its_digest(self) -> None:
        self.init()
        state = read_state(self.run_dir)
        contract = validate_scope_contract(self.run_dir, state)
        self.assertEqual(contract["owner_role"], "coordinator")
        self.assertEqual(state["policy_version"], 4)
        (self.run_dir / "verification-plan.json").write_text("{}\n", encoding="utf-8")
        (self.run_dir / "qa-plan.json").write_text("{}\n", encoding="utf-8")
        initial_digests = plan_digests(self.run_dir, state)
        self.assertIn("scope-contract.json", initial_digests)
        contract["out_of_scope"] = "changed"
        (self.run_dir / "scope-contract.json").write_text(json.dumps(contract), encoding="utf-8")
        altered_digest = hashlib.sha256(
            (json.dumps(contract, ensure_ascii=False, indent=2) + "\n").encode()).hexdigest()
        state["scope_contract_digest"] = altered_digest
        write_state(self.run_dir, state)
        with self.assertRaisesRegex(ValueError, "initialization marker"):
            read_state(self.run_dir)

    def test_policy_v4_allows_balanced_medium_single_area(self) -> None:
        self.init()
        state = read_state(self.run_dir)
        risk_guard(state)

    def test_result_renderer_accepts_scope_bound_policy_marker(self) -> None:
        self.init()
        state = read_state(self.run_dir)
        self.assertTrue(requires_test_code_assessment(state, self.run_dir))

    def test_policy_v4_light_requires_small_low_risk(self) -> None:
        self.init(depth="light", size="medium", expected=1)

    def test_policy_v4_balanced_requires_medium_task_size(self) -> None:
        for size in ("small", "large"):
            state = {"policy_version": 4, "version": 8, "risk_level": "low",
                     "risk_reason": "bounded changes", "scope": "both",
                     "task_size": size, "review_depth": "balanced", "events": []}
            with self.subTest(size=size), self.assertRaisesRegex(
                    ValueError, "medium single-area or low/medium two-area"):
                risk_guard(state)

    def test_design_review_rejects_third_distinct_digest(self) -> None:
        self.init()
        (self.run_dir / "backend-design.md").write_text("## 설계\nthird digest\n", encoding="utf-8")
        state = read_state(self.run_dir)
        state["events"].extend([
            {"type": "review", "area": "backend", "slot": 1, "digest": "first-digest"},
            {"type": "review", "area": "backend", "slot": 1, "digest": "second-digest"},
        ])
        write_state(self.run_dir, state)
        result = self.cli("review", "--area", "backend", "--result", "clear",
                          "--slot", "1", "--agent-id", "/root/reviewer",
                          "--focus", "comprehensive", "--risk-level", "medium",
                          expected=1)
        self.assertIn("design review limit reached", result.stderr)

    def test_orchestration_has_one_owner_and_balanced_single_area(self) -> None:
        plan = build_plan("backend", "immediate", "balanced")
        self.assertTrue(plan)
        self.assertEqual({task["owner_role"] for task in plan}, {"coordinator"})

    def test_light_qa_accepts_normal_and_regression_with_full_criterion_coverage(self) -> None:
        self.init(depth="light", size="small", risk="low")
        (self.run_dir / "official-sources.json").write_text(json.dumps({
            "schema_version": 1, "status": "not_applicable", "sources": [],
            "reason": "fixture tests plan size policy only"}), encoding="utf-8")
        (self.run_dir / "backend-design.md").write_text(
            "## 수용 기준\n- AC-1: 요청 동작을 처리한다\n", encoding="utf-8")
        (self.run_dir / "verification-plan.json").write_text(json.dumps({
            "backend": [{"id": "tests", "kind": "test", "command": "true", "required": True}]}),
            encoding="utf-8")
        (self.run_dir / "qa-plan.json").write_text(json.dumps({"backend": [
            {"slot": 1, "scenario_id": "normal", "criterion_id": "AC-1",
             "kind": "normal", "steps": "Run the requested flow", "expected": "It succeeds"},
            {"slot": 1, "scenario_id": "regression", "criterion_id": "AC-1",
             "kind": "regression", "steps": "Run the existing flow", "expected": "It remains stable"}]}),
            encoding="utf-8")
        state = read_state(self.run_dir)
        _, cases = plans(self.run_dir, state)
        self.assertEqual({case["kind"] for case in cases["backend"]}, {"normal", "regression"})

    def test_optional_pending_question_is_published_as_complete_follow_up(self) -> None:
        self.init()
        self.prepare_report_binding()
        self.cli("scope-question", "--question-id", "optional-doc", "--kind", "scope_extension",
                 "--question", "Add extra docs?", "--path", "docs/extra.md",
                 "--impact", "Outside current scope")
        published = self.cli("report-publish", "--draft-file", str(self.report_draft()))
        self.assertEqual(json.loads(published.stdout)["status"], "complete")
        report = (self.run_dir / "final-report.md").read_text(encoding="utf-8")
        self.assertIn("후속 작업으로 보류했습니다", report)

    def test_pending_required_decision_is_published_incomplete_without_closing_question(self) -> None:
        self.init()
        self.prepare_report_binding()
        self.cli("scope-question", "--question-id", "required-api", "--kind", "required_decision",
                 "--question", "Choose the required response behavior?",
                 "--path", "backend/api.py", "--dependent-path", "backend/api.py",
                 "--impact", "The requested behavior depends on this answer")
        published = self.cli("report-publish", "--draft-file", str(self.report_draft()))
        self.assertEqual(json.loads(published.stdout)["status"], "incomplete")
        register = json.loads((self.run_dir / "scope-register.json").read_text(encoding="utf-8"))
        self.assertEqual(register["questions"][0]["status"], "pending")

    def test_design_blocked_report_requires_current_binding(self) -> None:
        self.init()
        self.prepare_report_binding()
        (self.run_dir / "backend-design.md").write_text("## 설계\n차단된 설계\n", encoding="utf-8")
        state = read_state(self.run_dir)
        state["events"].append({"type": "design-blocked", "area": "backend",
                                "digest": design_digest(self.run_dir, "backend"),
                                "review_round": 2})
        write_state(self.run_dir, state)
        self.cli("report-publish", "--draft-file", str(self.report_draft()))
        status = json.loads(self.cli("status").stdout)
        self.assertEqual(status["stage"], "incomplete")
        self.assertEqual(status["next_action"], "start-new-run")
        self.cli("scope-question", "--question-id", "follow-up", "--kind", "scope_extension",
                 "--question", "Add one more out-of-scope idea?", "--path", "docs/follow-up.md",
                 "--impact", "Changes report binding")
        stale_status = json.loads(self.cli("status").stdout)
        self.assertEqual(stale_status["stage"], "report")
        self.assertEqual(stale_status["next_action"], "write-incomplete-report")

    def test_required_question_blocks_only_overlapping_task_paths(self) -> None:
        self.init()
        args = [sys.executable, str(ROOT / "scripts" / "workflow_harness.py"),
                "scope-question", "--run-dir", str(self.run_dir), "--question-id", "api-decision",
                "--kind", "required_decision", "--question", "Choose response shape",
                "--path", "backend/api.py", "--dependent-path", "backend/api.py",
                "--impact", "Implementation depends on the answer"]
        result = subprocess.run(args, capture_output=True, text=True, env=self.env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        guard = [sys.executable, str(ROOT / "scripts" / "workflow_harness.py"),
                 "task-guard", "--run-dir", str(self.run_dir), "--path"]
        independent = subprocess.run([*guard, "backend/tests"], capture_output=True,
                                     text=True, env=self.env)
        self.assertEqual(independent.returncode, 0, independent.stdout + independent.stderr)
        dependent = subprocess.run([*guard, "backend/api.py"], capture_output=True,
                                   text=True, env=self.env)
        self.assertEqual(dependent.returncode, 3, dependent.stdout + dependent.stderr)


if __name__ == "__main__":
    unittest.main()
