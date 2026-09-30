from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "scripts" / "workflow_harness.py"
sys.path.insert(0, str(ROOT / "scripts"))
import workflow_harness
from workflow_harness import changed_code_paths, validate_official_sources


class WorkflowHarnessV8Test(unittest.TestCase):
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
        self.environment = {**os.environ, "HOME": str(self.home)}
        self.call("init", "--repo", str(self.repo), "--scope", "backend",
                  "--review-mode", "immediate", "--mode-reference", "user chose immediate",
                  "--risk-level", "low", "--risk-reason", "isolated test fixture",
                  "--run-dir", str(self.run_dir))

    def call(self, *args: str, expected: int = 0) -> subprocess.CompletedProcess[str]:
        result = subprocess.run([sys.executable, str(HARNESS), *args], capture_output=True,
                                text=True, env=self.environment)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def set_sources_not_applicable(self) -> None:
        (self.run_dir / "official-sources.json").write_text(json.dumps({
            "schema_version": 1, "status": "not_applicable", "sources": [],
            "reason": "test fixture isolates scope tracking"}), encoding="utf-8")

    def add_design_and_plans(self) -> None:
        (self.run_dir / "backend-design.md").write_text("Design\n", encoding="utf-8")
        (self.run_dir / "verification-plan.json").write_text("{}\n", encoding="utf-8")
        (self.run_dir / "qa-plan.json").write_text("{}\n", encoding="utf-8")

    def test_scope_answers_are_bound_and_single_use(self) -> None:
        self.set_sources_not_applicable()
        self.add_design_and_plans()
        self.call("scope-question", "--run-dir", str(self.run_dir), "--question-id", "extra-doc",
                  "--kind", "scope_extension", "--question", "Add a migration guide?",
                  "--path", "docs/migration.md", "--impact", "Outside the requested change")
        state = json.loads((self.run_dir / "state.json").read_text(encoding="utf-8"))
        event = state["events"][-1]
        question = event["question_state"]
        arguments = ("scope-answer", "--run-dir", str(self.run_dir), "--question-id", "extra-doc",
                     "--run-id", state["run_id"], "--revision", str(question["revision"]),
                     "--question-digest", question["question_digest"], "--status", "declined",
                     "--answer", "Keep this out of scope", "--reference", "user selected decline")
        self.call(*arguments)
        register = json.loads((self.run_dir / "scope-register.json").read_text(encoding="utf-8"))
        self.assertEqual(register["questions"][0]["status"], "declined")
        recorded_state = json.loads((self.run_dir / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(recorded_state["events"][-2]["question_state"]["status"], "pending")
        self.assertEqual(recorded_state["events"][-1]["question_state"]["status"], "declined")
        self.call(*arguments, expected=1)
        draft = self.run_dir / "report-draft.md"
        draft.write_text("## 변경 파일 및 코드 위치\n변경 없음\n\n## 동작 원리\n기록만 수행\n\n"
                         "## 검증 결과\n테스트 실행\n\n## 요청 밖 문제 및 질문\n추가 범위 질문을 기록함\n", encoding="utf-8")
        result = self.call("report-publish", "--run-dir", str(self.run_dir),
                           "--draft-file", str(draft))
        self.assertEqual(json.loads(result.stdout)["status"], "complete")

    def test_pending_question_does_not_interrupt_earlier_workflow_stages(self) -> None:
        self.set_sources_not_applicable()
        self.call("scope-question", "--run-dir", str(self.run_dir), "--question-id", "optional-extra",
                  "--kind", "scope_extension", "--question", "Add extra docs?",
                  "--path", "docs/extra.md", "--impact", "Outside current request")
        status = json.loads(self.call("status", "--run-dir", str(self.run_dir)).stdout)
        self.assertNotEqual(status["next_action"], "answer-question")
        self.assertFalse(status["complete"])

    def test_legacy_interrupted_publish_reopens_pending_question(self) -> None:
        self.set_sources_not_applicable()
        self.add_design_and_plans()
        self.call("scope-question", "--run-dir", str(self.run_dir), "--question-id", "old-question",
                  "--kind", "scope_extension", "--question", "Add extra docs?",
                  "--path", "docs/extra.md", "--impact", "Outside current request")
        state = json.loads((self.run_dir / "state.json").read_text(encoding="utf-8"))
        question = json.loads((self.run_dir / "scope-register.json").read_text(encoding="utf-8"))["questions"][0]
        question.update({"status": "unanswered", "revision": 2})
        question["question_digest"] = workflow_harness.scope_question_digest(state["run_id"], question)
        state["events"].append({"type": "scope-answer", "run_id": state["run_id"],
                                "question_id": question["id"], "status": "unanswered",
                                "question_state": question,
                                "reference": "report publication closed the pending question"})
        state["events"].append({"type": "report-published", "run_id": state["run_id"],
                                "status": "complete", "report_digest": "a" * 64})
        staged = [workflow_harness.stage_transaction_file(
            self.run_dir, "final-report.md", b"old interrupted report\n"),
            workflow_harness.stage_transaction_file(
                self.run_dir, "scope-register.json",
                workflow_harness.serialized_json({"schema_version": 1, "questions": [question]})),
            workflow_harness.stage_transaction_file(
                self.run_dir, "state.json", workflow_harness.serialized_json(state))]
        workflow_harness.write_json_atomic(
            self.run_dir / ".report-publish.pending.json", {"schema_version": 1, "files": staged})
        for item in staged:
            os.replace(self.run_dir / item["temporary"], self.run_dir / item["target"])
        self.call("status", "--run-dir", str(self.run_dir))
        recovered_state = json.loads((self.run_dir / "state.json").read_text(encoding="utf-8"))
        register = json.loads((self.run_dir / "scope-register.json").read_text(encoding="utf-8"))
        self.assertEqual(register["questions"][0]["status"], "pending")
        self.assertFalse(any(event.get("type") == "scope-answer" for event in recovered_state["events"]))
        self.assertFalse(any(event.get("type") == "report-published" for event in recovered_state["events"]))
        self.assertFalse((self.run_dir / "final-report.md").exists())
        self.assertEqual(len(list(self.run_dir.glob("final-report-interrupted-*.md"))), 1)
        self.assertFalse((self.run_dir / ".report-publish.pending.json").exists())

    def test_completed_legacy_unanswered_report_without_journal_is_preserved(self) -> None:
        self.set_sources_not_applicable()
        self.call("scope-question", "--run-dir", str(self.run_dir), "--question-id", "historical-question",
                  "--kind", "scope_extension", "--question", "Add extra docs?",
                  "--path", "docs/extra.md", "--impact", "Outside current request")
        state = json.loads((self.run_dir / "state.json").read_text(encoding="utf-8"))
        question = json.loads((self.run_dir / "scope-register.json").read_text(encoding="utf-8"))["questions"][0]
        question.update({"status": "unanswered", "revision": 2})
        state["events"].append({"type": "scope-answer", "run_id": state["run_id"],
                                "question_id": question["id"], "status": "unanswered",
                                "question_state": question,
                                "reference": "report publication closed the pending question"})
        state["events"].append({"type": "report-published", "run_id": state["run_id"],
                                "status": "complete", "report_digest": "a" * 64})
        (self.run_dir / "state.json").write_bytes(workflow_harness.serialized_json(state))
        (self.run_dir / "scope-register.json").write_text(json.dumps(
            {"schema_version": 1, "questions": [question]}), encoding="utf-8")
        report = self.run_dir / "final-report.md"
        report.write_text("Historical completed report\n", encoding="utf-8")
        self.call("status", "--run-dir", str(self.run_dir))
        self.assertEqual((self.run_dir / "state.json").read_bytes(), workflow_harness.serialized_json(state))
        self.assertEqual(report.read_text(encoding="utf-8"), "Historical completed report\n")
        self.assertFalse(list(self.run_dir.glob("final-report-interrupted-*.md")))

    def test_new_publish_after_legacy_incomplete_answer_is_not_repaired_as_legacy(self) -> None:
        self.set_sources_not_applicable()
        self.add_design_and_plans()
        self.call("scope-question", "--run-dir", str(self.run_dir), "--question-id", "old-decision",
                  "--kind", "required_decision", "--question", "Which contract?",
                  "--dependent-path", "src/api.py", "--impact", "Implementation depends on this")
        state = json.loads((self.run_dir / "state.json").read_text(encoding="utf-8"))
        question = json.loads((self.run_dir / "scope-register.json").read_text(encoding="utf-8"))["questions"][0]
        question.update({"status": "unanswered", "revision": 2})
        question["question_digest"] = workflow_harness.scope_question_digest(state["run_id"], question)
        workflow_harness.write_json_atomic(self.run_dir / "scope-register.json",
                                           {"schema_version": 1, "questions": [question]})
        previous_report = "Earlier incomplete report\n"
        (self.run_dir / "final-report.md").write_text(previous_report, encoding="utf-8")
        binding = dict(workflow_harness.current_binding(self.run_dir, state))
        binding["questions"] = workflow_harness.hashlib.sha256(
            (self.run_dir / "scope-register.json").read_bytes()).hexdigest()
        state["events"].append({"type": "scope-answer", "run_id": state["run_id"],
                                "question_id": question["id"], "status": "unanswered",
                                "question_state": question,
                                "reference": "report publication closed the pending question"})
        state["events"].append({"type": "report-published", "run_id": state["run_id"],
                                "status": "incomplete", "binding": binding,
                                "report_digest": workflow_harness.hashlib.sha256(
                                    previous_report.encode()).hexdigest()})
        workflow_harness.write_state(self.run_dir, state)
        self.call("scope-answer", "--run-dir", str(self.run_dir), "--question-id", question["id"],
                  "--run-id", state["run_id"], "--revision", str(question["revision"]),
                  "--question-digest", question["question_digest"], "--status", "approved",
                  "--answer", "Use current contract", "--reference", "user approval")
        draft = self.run_dir / "report-draft.md"
        draft.write_text("## 변경 파일 및 코드 위치\n변경 없음\n\n## 동작 원리\n기존 계약을 사용\n\n"
                         "## 검증 결과\n요청 범위 결과를 기록\n\n## 요청 밖 문제 및 질문\n없음\n",
                         encoding="utf-8")
        result = self.call("report-publish", "--run-dir", str(self.run_dir),
                           "--draft-file", str(draft))
        self.assertEqual(json.loads(result.stdout)["status"], "complete")
        self.assertTrue((self.run_dir / "final-report.md").is_file())
        self.assertFalse(list(self.run_dir.glob("final-report-interrupted-*.md")))
        state = json.loads((self.run_dir / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["events"][-1]["type"], "report-published")
        self.assertEqual(state["events"][-1]["status"], "complete")

    def test_legacy_recovery_does_not_reopen_historical_closure_before_new_publish(self) -> None:
        self.set_sources_not_applicable()
        self.call("scope-question", "--run-dir", str(self.run_dir), "--question-id", "old-decision",
                  "--kind", "required_decision", "--question", "Which contract?",
                  "--dependent-path", "src/api.py", "--impact", "Implementation depends on this")
        state = json.loads((self.run_dir / "state.json").read_text(encoding="utf-8"))
        question = json.loads((self.run_dir / "scope-register.json").read_text(encoding="utf-8"))["questions"][0]
        self.call("scope-answer", "--run-dir", str(self.run_dir), "--question-id", question["id"],
                  "--run-id", state["run_id"], "--revision", str(question["revision"]),
                  "--question-digest", question["question_digest"], "--status", "approved",
                  "--answer", "Use current contract", "--reference", "user approval")

        state = json.loads((self.run_dir / "state.json").read_text(encoding="utf-8"))
        approved_event = state["events"].pop()
        unanswered = dict(question, status="unanswered", revision=2)
        unanswered["question_digest"] = workflow_harness.scope_question_digest(
            state["run_id"], unanswered)
        state["events"].extend([
            {"type": "scope-answer", "run_id": state["run_id"],
             "question_id": question["id"], "status": "unanswered",
             "question_state": unanswered,
             "reference": "report publication closed the pending question"},
            {"type": "report-published", "run_id": state["run_id"],
             "status": "incomplete", "report_digest": "a" * 64},
            approved_event])
        latest_report = "Current report after user approval\n"
        state["events"].append({"type": "report-published", "run_id": state["run_id"],
                                "status": "complete", "report_digest": workflow_harness.hashlib.sha256(
                                    latest_report.encode()).hexdigest()})
        staged = [workflow_harness.stage_transaction_file(
            self.run_dir, "final-report.md", latest_report.encode()),
            workflow_harness.stage_transaction_file(
            self.run_dir, "scope-register.json",
                workflow_harness.serialized_json(json.loads(
                    (self.run_dir / "scope-register.json").read_text(encoding="utf-8")))),
            workflow_harness.stage_transaction_file(
                self.run_dir, "state.json", workflow_harness.serialized_json(state))]
        workflow_harness.write_json_atomic(
            self.run_dir / ".report-publish.pending.json", {"schema_version": 1, "files": staged})
        for item in staged:
            os.replace(self.run_dir / item["temporary"], self.run_dir / item["target"])

        self.call("status", "--run-dir", str(self.run_dir))

        recovered = json.loads((self.run_dir / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(recovered["events"][-1]["type"], "report-published")
        self.assertEqual(recovered["events"][-1]["status"], "complete")
        self.assertEqual((self.run_dir / "final-report.md").read_text(encoding="utf-8"), latest_report)
        self.assertFalse(list(self.run_dir.glob("final-report-interrupted-*.md")))
        self.assertFalse((self.run_dir / ".report-publish-recovery.pending.json").exists())

    def test_legacy_recovery_retry_rebuilds_register_after_partial_repair(self) -> None:
        self.set_sources_not_applicable()
        self.call("scope-question", "--run-dir", str(self.run_dir), "--question-id", "retry-question",
                  "--kind", "scope_extension", "--question", "Add extra docs?",
                  "--path", "docs/extra.md", "--impact", "Outside current request")
        state = json.loads((self.run_dir / "state.json").read_text(encoding="utf-8"))
        pending = json.loads((self.run_dir / "scope-register.json").read_text(encoding="utf-8"))["questions"][0]
        closed = dict(pending, status="unanswered", revision=2)
        closed["question_digest"] = workflow_harness.scope_question_digest(state["run_id"], closed)
        state["events"].append({"type": "scope-answer", "run_id": state["run_id"],
                                "question_id": pending["id"], "status": "unanswered",
                                "question_state": closed,
                                "reference": "report publication closed the pending question"})
        state["events"].pop()
        workflow_harness.write_state(self.run_dir, state)
        (self.run_dir / "scope-register.json").write_text(json.dumps(
            {"schema_version": 1, "questions": [closed]}), encoding="utf-8")
        (self.run_dir / "final-report.md").write_text("interrupted report\n", encoding="utf-8")
        os.replace(self.run_dir / "final-report.md",
                   self.run_dir / "final-report-interrupted-existing.md")
        workflow_harness.write_json_atomic(
            self.run_dir / ".report-publish-recovery.pending.json", {"schema_version": 1})
        workflow_harness.recover_report_publish(self.run_dir, repair_legacy=True)
        self.assertFalse((self.run_dir / ".report-publish-recovery.pending.json").exists())
        recovered = json.loads((self.run_dir / "scope-register.json").read_text(encoding="utf-8"))
        self.assertEqual(recovered["questions"][0]["status"], "pending")

    def test_required_decision_must_be_explicitly_closed_before_publish(self) -> None:
        self.set_sources_not_applicable()
        self.add_design_and_plans()
        self.call("scope-question", "--run-dir", str(self.run_dir), "--question-id", "api-contract",
                  "--kind", "required_decision", "--question", "Which contract should be used?",
                  "--dependent-path", "src/api.py", "--impact", "Implementation depends on this choice")
        draft = self.run_dir / "report-draft.md"
        draft.write_text("## 변경 파일 및 코드 위치\n변경 없음\n\n## 동작 원리\n미결정 항목은 보류\n\n"
                         "## 검증 결과\n요청 범위 검증 결과를 기록\n\n"
                         "## 요청 밖 문제 및 질문\n필수 결정을 질문함\n", encoding="utf-8")
        original_state = (self.run_dir / "state.json").read_bytes()
        original_register = (self.run_dir / "scope-register.json").read_bytes()
        self.call("report-publish", "--run-dir", str(self.run_dir),
                  "--draft-file", str(draft), expected=1)
        self.assertEqual((self.run_dir / "state.json").read_bytes(), original_state)
        self.assertEqual((self.run_dir / "scope-register.json").read_bytes(), original_register)
        self.assertFalse((self.run_dir / "final-report.md").exists())
        state = json.loads((self.run_dir / "state.json").read_text(encoding="utf-8"))
        question_events = [event for event in state["events"] if event.get("type") == "scope-question"]
        answer_events = [event for event in state["events"] if event.get("type") == "scope-answer"]
        self.assertEqual(question_events[-1]["question_state"]["status"], "pending")
        self.assertEqual(answer_events, [])
        self.assertFalse(any(event.get("type") == "final-gate-passed" for event in state["events"]))
        question = json.loads((self.run_dir / "scope-register.json").read_text(encoding="utf-8"))["questions"][0]
        answer_prefix = ("scope-answer", "--run-dir", str(self.run_dir), "--question-id", "api-contract",
                         "--run-id", state["run_id"])
        decline = self.call(*answer_prefix, "--revision", str(question["revision"]),
                            "--question-digest", question["question_digest"], "--status", "declined",
                            "--answer", "Need more time", "--reference", "user deferred")
        self.assertEqual(json.loads(decline.stdout)["status"], "declined")
        result = self.call("report-publish", "--run-dir", str(self.run_dir),
                           "--draft-file", str(draft))
        self.assertEqual(json.loads(result.stdout)["status"], "incomplete")
        report = (self.run_dir / "final-report.md").read_text(encoding="utf-8")
        self.assertIn("declined", report)
        previous_plans = workflow_harness.plan_digests(
            self.run_dir, json.loads((self.run_dir / "state.json").read_text(encoding="utf-8")))
        self.call("report-publish", "--run-dir", str(self.run_dir), "--draft-file", str(draft))
        question = json.loads((self.run_dir / "scope-register.json").read_text(encoding="utf-8"))["questions"][0]
        approve = self.call(*answer_prefix, "--revision", str(question["revision"]),
                            "--question-digest", question["question_digest"], "--status", "approved",
                            "--answer", "Use the existing contract", "--reference", "user resumed and approved")
        self.assertEqual(json.loads(approve.stdout)["status"], "approved")
        revised = json.loads((self.run_dir / "state.json").read_text(encoding="utf-8"))
        answer_events = [event for event in revised["events"] if event.get("type") == "scope-answer"]
        self.assertEqual([event["question_state"]["status"] for event in answer_events],
                         ["declined", "approved"])
        self.assertNotEqual(previous_plans["approved-scope"],
                            workflow_harness.plan_digests(self.run_dir, revised)["approved-scope"])
        self.assertFalse(workflow_harness.has_current_incomplete_report(
            self.run_dir, revised, workflow_harness.scope_register(self.run_dir, revised)))
        self.call("scope-answer", "--run-dir", str(self.run_dir), "--question-id", "api-contract",
                  "--run-id", state["run_id"], "--revision", str(question["revision"]),
                  "--question-digest", question["question_digest"], "--status", "approved",
                  "--answer", "stale", "--reference", "stale response", expected=1)

    def test_report_validation_preserves_previous_report(self) -> None:
        self.set_sources_not_applicable()
        (self.repo / "new.py").write_text("value = 2\n", encoding="utf-8")
        prior = "previous complete report\n"
        (self.run_dir / "final-report.md").write_text(prior, encoding="utf-8")
        draft = self.run_dir / "report-draft.md"
        draft.write_text("## 변경 파일 및 코드 위치\n파일 목록 없음\n\n## 동작 원리\n코드 변경을 확인\n\n"
                         "## 검증 결과\n변경 경로를 확인\n\n## 요청 밖 문제 및 질문\n없음\n", encoding="utf-8")
        result = self.call("report-publish", "--run-dir", str(self.run_dir),
                           "--draft-file", str(draft), expected=1)
        self.assertIn("omits changed code paths", result.stderr)
        self.assertEqual((self.run_dir / "final-report.md").read_text(encoding="utf-8"), prior)

    def test_report_publish_records_custom_effective_hooks_path(self) -> None:
        self.set_sources_not_applicable()
        self.add_design_and_plans()
        custom_hooks = self.home / "custom-hooks"
        custom_hooks.mkdir()
        subprocess.run(["git", "-C", str(self.repo), "config", "--local", "core.hooksPath",
                        str(custom_hooks)], check=True)
        draft = self.run_dir / "report-draft.md"
        draft.write_text("## 변경 파일 및 코드 위치\n변경 없음\n\n## 동작 원리\n요청 정책 기록\n\n"
                         "## 검증 결과\n검증 결과 기록\n\n## 요청 밖 문제 및 질문\n없음\n", encoding="utf-8")
        self.call("report-publish", "--run-dir", str(self.run_dir), "--draft-file", str(draft))
        report = (self.run_dir / "final-report.md").read_text(encoding="utf-8")
        self.assertIn("## 로컬 문서 guard 훅 설정", report)
        self.assertIn(str(custom_hooks.resolve()), report)
        self.assertIn("설정과 기존 훅은 변경하지 않았습니다", report)

    def test_report_publish_records_default_hooks_path_when_unconfigured(self) -> None:
        self.set_sources_not_applicable()
        self.add_design_and_plans()
        draft = self.run_dir / "report-draft.md"
        draft.write_text("## 변경 파일 및 코드 위치\n변경 없음\n\n## 동작 원리\n요청 정책 기록\n\n"
                         "## 검증 결과\n검증 결과 기록\n\n## 요청 밖 문제 및 질문\n없음\n", encoding="utf-8")
        self.call("report-publish", "--run-dir", str(self.run_dir), "--draft-file", str(draft))
        report = (self.run_dir / "final-report.md").read_text(encoding="utf-8")
        self.assertIn("## 로컬 문서 guard 훅 설정", report)
        self.assertIn("Git 기본 경로", report)
        self.assertIn("core.hooksPath` 미설정", report)

    def test_agent_effort_can_be_recorded_as_unavailable(self) -> None:
        result = workflow_harness.model_selection(
            "GPT-6 (exact variant unavailable)", "unavailable", "runtime does not expose effort")
        self.assertEqual(result["effort"], "unavailable")

    def test_historical_check_manifest_keeps_its_original_plan_binding(self) -> None:
        state = json.loads((self.run_dir / "state.json").read_text(encoding="utf-8"))
        old_design = "a" * 64
        old_plans = {"verification-plan.json": "b" * 64}
        log = self.run_dir / "historical-check.log"
        log.write_text("historical check passed\n", encoding="utf-8")
        manifest = {
            "schema_version": 2, "attempt_id": "1" * 32, "command": "check-old-plan",
            "started_at": "2026-09-28T00:00:00+00:00", "finished_at": "2026-09-28T00:00:01+00:00",
            "exit_code": 0, "status": "pass", "log_file": log.name,
            "log_sha256": workflow_harness.hashlib.sha256(log.read_bytes()).hexdigest(),
            "repo_root": str(self.repo.resolve()), "area": "backend", "code_digest": "c" * 64,
            "run_id": state["run_id"], "checkout_id": state["checkout_ids"]["backend"],
            "design_digest": old_design, "plan_digests": old_plans,
        }
        manifest_path = self.run_dir / "historical-check.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        manifest_name, manifest_hash = workflow_harness.evidence_digest(self.run_dir, str(manifest_path))
        _, log_hash = workflow_harness.evidence_digest(self.run_dir, str(log))
        event = {"area": "backend", "name": "old-check", "status": "pass",
                 "manifest_file": manifest_name, "manifest_digest": manifest_hash,
                 "attempt_id": manifest["attempt_id"], "planned_command": manifest["command"],
                 "digest": manifest["code_digest"], "design_digest": old_design,
                 "plans": old_plans, "evidence_file": log.name, "evidence_digest": log_hash}
        with patch.object(workflow_harness, "design_digest", return_value="d" * 64), \
                patch.object(workflow_harness, "plan_digests", return_value={"verification-plan.json": "e" * 64}):
            with self.assertRaisesRegex(ValueError, "current run, design, or plan"):
                workflow_harness.verify_manifest_binding(self.run_dir, state, event)
            workflow_harness.verify_manifest_binding(self.run_dir, state, event, historical=True)

    def test_report_sections_need_contents_and_code_line_locations(self) -> None:
        valid = ("## 변경 파일 및 코드 위치\nbackend:new.py:new:4 - changed value\n\n"
                 "## 동작 원리\nReads and returns the value.\n\n"
                 "## 검증 결과\nUnit tests passed.\n\n"
                 "## 요청 밖 문제 및 질문\n없음\n")
        with self.assertRaisesRegex(ValueError, "line location"):
            from workflow_harness import validate_report_sections
            validate_report_sections(valid.replace("backend:new.py:new:4", "backend:new.py"),
                                     ["backend:new.py"])
        with self.assertRaisesRegex(ValueError, "section is empty"):
            validate_report_sections(valid.replace("Reads and returns the value.", ""), [])

    def test_report_line_location_must_point_to_a_changed_hunk(self) -> None:
        (self.repo / "app.py").write_text("value = 1\nvalue = 2\n", encoding="utf-8")
        valid = ("## 변경 파일 및 코드 위치\nbackend:app.py:new:2 - changed value\n\n"
                 "## 동작 원리\nReads the changed value.\n\n## 검증 결과\nPassed.\n\n"
                 "## 요청 밖 문제 및 질문\n없음\n")
        locations = {"backend:app.py": workflow_harness.changed_code_lines(
            json.loads((self.run_dir / "state.json").read_text(encoding="utf-8")), "backend:app.py")}
        from workflow_harness import validate_report_sections
        validate_report_sections(valid, ["backend:app.py"], locations)
        fabricated = valid.replace("backend:app.py:new:2", "backend:app.py:new:999")
        with self.assertRaisesRegex(ValueError, "not a changed code line"):
            validate_report_sections(fabricated, ["backend:app.py"], locations)
        mixed = valid.replace("backend:app.py:new:2",
                              "backend:app.py:new:2 and backend:app.py:new:999")
        with self.assertRaisesRegex(ValueError, "not a changed code line"):
            validate_report_sections(mixed, ["backend:app.py"], locations)
        (self.repo / "app.py").write_text("", encoding="utf-8")
        deleted_locations = {"backend:app.py": workflow_harness.changed_code_lines(
            json.loads((self.run_dir / "state.json").read_text(encoding="utf-8")), "backend:app.py")}
        baseline = json.loads((self.run_dir / "state.json").read_text(encoding="utf-8"))["base_commits"]["backend"]
        deleted = valid.replace("backend:app.py:new:2", f"backend:app.py:old:1 (baseline:{baseline})")
        validate_report_sections(deleted, ["backend:app.py"], deleted_locations,
                                 {"backend": baseline})
        fabricated_baseline = deleted.replace(baseline, "0" * 40)
        with self.assertRaisesRegex(ValueError, "invalid baseline"):
            validate_report_sections(fabricated_baseline, ["backend:app.py"], deleted_locations,
                                     {"backend": baseline})

    def test_report_change_paths_include_separate_integration_repository(self) -> None:
        repos = {}
        for area in ("frontend", "backend", "integration"):
            repo = self.home / area
            repo.mkdir()
            subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
            (repo / "base.txt").write_text(area, encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "base.txt"], check=True)
            subprocess.run(["git", "-C", str(repo), "-c", "core.hooksPath=/dev/null",
                            "-c", "user.name=Test",
                            "-c", "user.email=test@example.test", "commit", "-qm", "initial"], check=True)
            (repo / f"{area}.py").write_text("value = 1\n", encoding="utf-8")
            repos[area] = repo
        state = {"version": 8, "scope": "both",
                 "repo_roots": {key: str(value) for key, value in repos.items()},
                 "repo_root": str(repos["backend"]), "base_commits": {}}
        self.assertEqual(changed_code_paths(state), ["backend:backend.py", "frontend:frontend.py",
                                                     "integration:integration.py"])
        conflicts = workflow_harness.hook_path_conflicts(state)
        self.assertEqual({area for area, _, _ in conflicts}, {"backend", "frontend", "integration"})

    def test_official_source_requires_detected_version_evidence(self) -> None:
        valid = {"schema_version": 1, "status": "applicable", "sources": [{
            "id": "vendor-doc", "publisher": "Vendor", "title": "API reference",
            "url": "https://vendor.example/docs", "version": "2.0", "checked_on": "2026-09-28",
            "detected_version": "2.0", "version_source": "pyproject.toml dependency lock",
            "claims": ["API behavior"], "applied_to": ["src/api.py"]}]}
        (self.run_dir / "official-sources.json").write_text(json.dumps(valid), encoding="utf-8")
        validate_official_sources(self.run_dir)
        for field, message in (("detected_version", "detected version"),
                               ("claims", "claims"), ("applied_to", "applied files")):
            incomplete = json.loads(json.dumps(valid))
            del incomplete["sources"][0][field]
            (self.run_dir / "official-sources.json").write_text(json.dumps(incomplete), encoding="utf-8")
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, message):
                validate_official_sources(self.run_dir)

    def test_official_source_manifest_rejects_non_string_status(self) -> None:
        for status in ([], {}):
            with self.subTest(status=status):
                (self.run_dir / "official-sources.json").write_text(json.dumps({
                    "schema_version": 1, "status": status, "sources": []
                }), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "invalid official source manifest"):
                    validate_official_sources(self.run_dir)

    def test_report_publish_renders_official_claims_and_applied_paths(self) -> None:
        self.add_design_and_plans()
        source = {"id": "python-ref", "publisher": "Python", "title": "Python 3.14 docs",
                  "url": "https://docs.python.org/3.14/>) <script>alert(1)</script>", "version": "3.14",
                  "checked_on": "2026-09-29", "detected_version": "3.14.6",
                  "version_source": "python3 --version\n\n## Injected status",
                  "claims": ["pathlib behavior", "[official guidance](https://attacker.example)",
                             "<script>"],
                  "applied_to": ["scripts/workflow_harness.py:hook_path_conflicts"]}
        (self.run_dir / "official-sources.json").write_text(json.dumps({
            "schema_version": 1, "status": "applicable", "sources": [source]
        }), encoding="utf-8")
        draft = self.run_dir / "report-draft.md"
        draft.write_text("## 변경 파일 및 코드 위치\n변경 없음\n\n## 동작 원리\n요청 정책 기록\n\n"
                         "## 검증 결과\n검증 결과 기록\n\n## 요청 밖 문제 및 질문\n없음\n", encoding="utf-8")
        self.call("report-publish", "--run-dir", str(self.run_dir), "--draft-file", str(draft))
        report = (self.run_dir / "final-report.md").read_text(encoding="utf-8")
        self.assertIn("https://docs.python.org/3.14/", report)
        self.assertIn(r"감지 버전 3\.14\.6", report)
        self.assertIn(r"python3 \-\-version", report)
        self.assertIn("주장: pathlib behavior", report)
        self.assertNotIn("[official guidance](https://attacker.example)", report)
        self.assertNotIn("<script>", report)
        self.assertNotIn("</script>", report)
        self.assertIn(r"scripts/workflow\_harness\.py:hook\_path\_conflicts", report)
        self.assertNotIn("\n## Injected status\n", report)

    def test_report_publish_recovers_after_each_target_replace_interruption(self) -> None:
        self.set_sources_not_applicable()
        self.add_design_and_plans()
        draft = self.run_dir / "report-draft.md"
        draft.write_text("## 변경 파일 및 코드 위치\n변경 없음\n\n## 동작 원리\n요청된 정책을 기록\n\n"
                         "## 검증 결과\n테스트 통과\n\n## 요청 밖 문제 및 질문\n없음\n", encoding="utf-8")
        original_replace = workflow_harness.os.replace
        for target_name in ("final-report.md", "scope-register.json", "state.json"):
            with self.subTest(target=target_name):
                state = json.loads((self.run_dir / "state.json").read_text(encoding="utf-8"))
                previous_report_count = sum(event.get("type") == "report-published"
                                            for event in state["events"])
                failed = False

                def interrupt_target_replace(source: str | Path, target: str | Path) -> None:
                    nonlocal failed
                    if Path(target).name == target_name and not failed:
                        failed = True
                        raise OSError(f"simulated {target_name} interruption")
                    original_replace(source, target)

                with patch.object(workflow_harness.os, "replace", side_effect=interrupt_target_replace):
                    with self.assertRaisesRegex(OSError, "simulated"):
                        workflow_harness.publish_report(self.run_dir, state, draft)
                self.assertTrue((self.run_dir / ".report-publish.pending.json").is_file())
                self.call("status", "--run-dir", str(self.run_dir))
                recovered = json.loads((self.run_dir / "state.json").read_text(encoding="utf-8"))
                events = [event for event in recovered["events"] if event.get("type") == "report-published"]
                self.assertEqual(len(events), previous_report_count + 1)
                report = (self.run_dir / "final-report.md").read_bytes()
                self.assertEqual(workflow_harness.hashlib.sha256(report).hexdigest(),
                                 events[-1]["report_digest"])
                self.assertFalse((self.run_dir / ".report-publish.pending.json").exists())

    def test_report_publish_recovers_after_state_replace_before_journal_removal(self) -> None:
        self.set_sources_not_applicable()
        self.add_design_and_plans()
        draft = self.run_dir / "report-draft.md"
        draft.write_text("## 변경 파일 및 코드 위치\n변경 없음\n\n## 동작 원리\n요청된 정책을 기록\n\n"
                         "## 검증 결과\n테스트 통과\n\n## 요청 밖 문제 및 질문\n없음\n", encoding="utf-8")
        original_unlink = Path.unlink
        failed = False

        def interrupt_journal_removal(path: Path, *args, **kwargs) -> None:
            nonlocal failed
            if path.name == ".report-publish.pending.json" and not failed:
                failed = True
                raise OSError("simulated journal cleanup interruption")
            original_unlink(path, *args, **kwargs)

        state = json.loads((self.run_dir / "state.json").read_text(encoding="utf-8"))
        with patch.object(Path, "unlink", interrupt_journal_removal):
            with self.assertRaisesRegex(OSError, "simulated journal cleanup interruption"):
                workflow_harness.publish_report(self.run_dir, state, draft)
        self.assertTrue((self.run_dir / ".report-publish.pending.json").is_file())
        self.call("status", "--run-dir", str(self.run_dir))
        state = json.loads((self.run_dir / "state.json").read_text(encoding="utf-8"))
        events = [event for event in state["events"] if event.get("type") == "report-published"]
        self.assertEqual(len(events), 1)
        report = (self.run_dir / "final-report.md").read_bytes()
        self.assertEqual(workflow_harness.hashlib.sha256(report).hexdigest(), events[0]["report_digest"])
        self.assertFalse((self.run_dir / ".report-publish.pending.json").exists())

    def test_legacy_migration_recovers_all_staged_files_after_interruption(self) -> None:
        state_file = self.run_dir / "state.json"
        legacy = json.loads(state_file.read_text(encoding="utf-8"))
        legacy["version"] = 7
        legacy_snapshot = json.loads(json.dumps(legacy))
        state_file.write_text(json.dumps(legacy), encoding="utf-8")
        original_replace = workflow_harness.os.replace
        failed = False

        def interrupt_scope_replace(source: str | Path, target: str | Path) -> None:
            nonlocal failed
            if Path(target).name == "scope-register.json" and not failed:
                failed = True
                raise OSError("simulated migration interruption")
            original_replace(source, target)

        with patch.object(workflow_harness.os, "replace", side_effect=interrupt_scope_replace):
            with self.assertRaisesRegex(OSError, "simulated migration interruption"):
                workflow_harness.migrate_run_to_v8(self.run_dir, json.loads(json.dumps(legacy)))
        self.assertEqual(json.loads(state_file.read_text(encoding="utf-8"))["version"], 7)
        self.assertTrue((self.run_dir / ".legacy-migration.pending.json").is_file())
        workflow_harness.recover_legacy_migration(self.run_dir)
        migrated = json.loads(state_file.read_text(encoding="utf-8"))
        snapshot = json.loads((self.run_dir / "legacy-state-v7.json").read_text(encoding="utf-8"))
        self.assertEqual(migrated["version"], 8)
        self.assertEqual(snapshot, legacy_snapshot)
        self.assertTrue((self.run_dir / "official-sources.json").is_file())
        self.assertTrue((self.run_dir / "scope-register.json").is_file())
        self.assertFalse((self.run_dir / ".legacy-migration.pending.json").exists())

    def test_final_gate_cannot_pass_without_any_gate_evidence(self) -> None:
        self.call("check", "--run-dir", str(self.run_dir), "--gate", "final", expected=1)
        status = json.loads(self.call("status", "--run-dir", str(self.run_dir)).stdout)
        state = json.loads((self.run_dir / "state.json").read_text(encoding="utf-8"))
        self.assertFalse(status["complete"])
        self.assertFalse(any(event.get("type") == "final-gate-passed" for event in state["events"]))

    def test_new_run_manifests_appear_together(self) -> None:
        self.assertTrue((self.run_dir / "state.json").is_file())
        self.assertTrue((self.run_dir / "official-sources.json").is_file())
        self.assertTrue((self.run_dir / "scope-register.json").is_file())
        policy = json.loads((self.run_dir / "run-policy.json").read_text(encoding="utf-8"))
        state = json.loads((self.run_dir / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(policy, workflow_harness.run_policy_data(state["run_id"], 3))
        self.assertFalse(any(self.run_dir.parent.glob(".workflow-init-*")))

    def test_exact_resume_promotes_incomplete_legacy_run_with_snapshot(self) -> None:
        state_file = self.run_dir / "state.json"
        legacy = json.loads(state_file.read_text(encoding="utf-8"))
        legacy["version"] = 7
        state_file.write_text(json.dumps(legacy), encoding="utf-8")
        self.call("init", "--resume-run", str(self.run_dir))
        migrated = json.loads(state_file.read_text(encoding="utf-8"))
        snapshot = json.loads((self.run_dir / "legacy-state-v7.json").read_text(encoding="utf-8"))
        self.assertEqual(migrated["version"], 8)
        self.assertEqual(migrated["run_id"], legacy["run_id"])
        self.assertEqual(snapshot, legacy)

    def test_legacy_migration_requires_explicit_valid_baseline(self) -> None:
        state_file = self.run_dir / "state.json"
        legacy = json.loads(state_file.read_text(encoding="utf-8"))
        legacy["version"] = 6
        baseline = legacy["base_commits"].pop("backend")
        (self.repo / "committed-change.py").write_text("value = 3\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repo), "add", "committed-change.py"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "-c", "user.name=Test",
                        "-c", "user.email=test@example.test", "commit", "-qm", "legacy implementation"],
                       check=True)
        state_file.write_text(json.dumps(legacy), encoding="utf-8")
        self.call("init", "--resume-run", str(self.run_dir), expected=1)
        self.assertIn('"version": 6', state_file.read_text(encoding="utf-8"))
        self.call("init", "--resume-run", str(self.run_dir), "--legacy-base", f"backend={baseline}")
        migrated = json.loads(state_file.read_text(encoding="utf-8"))
        self.assertEqual(migrated["base_commits"]["backend"], baseline)
        self.assertIn("backend:committed-change.py", changed_code_paths(migrated))


if __name__ == "__main__":
    unittest.main()
