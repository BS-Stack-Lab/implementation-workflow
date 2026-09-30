from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import render_result
import workflow_harness


class TestCodeAssessmentTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="test-code-assessment-")
        self.addCleanup(self.tempdir.cleanup)
        self.run_dir = Path(self.tempdir.name)
        self.state = {"version": 8, "policy_version": 2, "run_id": "v8p2-run-policy-test",
                      "review_depth": "light", "scope": "backend"}
        (self.run_dir / "run-policy.json").write_text(json.dumps(
            workflow_harness.run_policy_data(self.state["run_id"], 2)), encoding="utf-8")
        self.result = {"area": "backend", "test_code_assessment": {
            "outcome": "verified", "rationale": "The changed behavior has direct tests.",
            "acceptance_criteria": ["R1"], "required_test_ids": ["unit"],
            "change_paths": ["backend:src/service.py", "backend:tests/test_service.py"],
            "reviewed_test_paths": ["backend:tests/test_service.py"],
            "test_path_evidence": [{"path": "backend:tests/test_service.py",
                                    "location": "test_updates_validation",
                                    "test_behavior": "rejects invalid service input"}],
            "omission_category": None}}
        self.patches = [
            patch.object(render_result, "plans", return_value=({"backend": [
                {"id": "unit", "kind": "test", "required": True},
                {"id": "lint", "kind": "lint", "required": True}]}, {})),
            patch.object(render_result, "acceptance_ids", return_value={"R1"}),
            patch.object(render_result, "changed_code_paths", return_value={
                "backend:src/service.py", "backend:tests/test_service.py"})]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def test_valid_assessment_covers_criteria_checks_and_changed_tests(self) -> None:
        render_result.validate_test_code_assessment(self.run_dir, self.state, self.result)

    def test_missing_assessment_is_rejected(self) -> None:
        self.result.pop("test_code_assessment")
        with self.assertRaisesRegex(ValueError, "needs test_code_assessment"):
            render_result.validate_test_code_assessment(self.run_dir, self.state, self.result)

    def test_empty_required_test_ids_are_rejected(self) -> None:
        self.result["test_code_assessment"]["required_test_ids"] = []
        with self.assertRaisesRegex(ValueError, "non-empty string array"):
            render_result.validate_test_code_assessment(self.run_dir, self.state, self.result)

    def test_omitting_a_changed_path_is_rejected(self) -> None:
        self.result["test_code_assessment"]["change_paths"] = ["backend:tests/test_service.py"]
        with self.assertRaisesRegex(ValueError, "every changed path"):
            render_result.validate_test_code_assessment(self.run_dir, self.state, self.result)

    def test_unusual_test_path_needs_explicit_test_locator(self) -> None:
        self.result["test_code_assessment"]["reviewed_test_paths"] = ["backend:src/service.py"]
        self.result["test_code_assessment"]["test_path_evidence"] = []
        with self.assertRaisesRegex(ValueError, "needs test_path_evidence"):
            render_result.validate_test_code_assessment(self.run_dir, self.state, self.result)
        self.result["test_code_assessment"]["test_path_evidence"] = [{
            "path": "backend:src/service.py", "location": "inline test module",
            "test_behavior": "asserts public behavior for invalid input"}]
        render_result.validate_test_code_assessment(self.run_dir, self.state, self.result)

    def test_invalid_json_types_produce_validation_errors(self) -> None:
        self.result["test_code_assessment"]["outcome"] = []
        with self.assertRaisesRegex(ValueError, "valid outcome"):
            render_result.validate_test_code_assessment(self.run_dir, self.state, self.result)
        assessment = self.result["test_code_assessment"]
        assessment.update({"outcome": "not-needed", "reviewed_test_paths": [],
                           "test_path_evidence": [], "omission_category": {}})
        with self.assertRaisesRegex(ValueError, "allowed omission_category"):
            render_result.validate_test_code_assessment(self.run_dir, self.state, self.result)

    def test_policy_version_pins_new_run_requirement(self) -> None:
        self.state.pop("test_code_assessment_required", None)
        self.assertTrue(render_result.requires_test_code_assessment(self.state, self.run_dir))
        self.state["policy_version"] = 1
        with self.assertRaisesRegex(ValueError, "does not match"):
            render_result.requires_test_code_assessment(self.state, self.run_dir)
        (self.run_dir / "run-policy.json").write_text(json.dumps(
            workflow_harness.run_policy_data(self.state["run_id"], 1)), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "identity does not match"):
            render_result.requires_test_code_assessment(self.state, self.run_dir)
        (self.run_dir / "run-policy.json").unlink()
        self.state["run_id"] = "legacy-run-id"
        self.assertFalse(render_result.requires_test_code_assessment(self.state, self.run_dir))
        self.state.pop("policy_version")
        with self.assertRaisesRegex(ValueError, "integer policy_version"):
            render_result.requires_test_code_assessment(self.state, self.run_dir)

    def test_new_policy_requires_valid_marker(self) -> None:
        marker = self.run_dir / "run-policy.json"
        marker.unlink()
        with self.assertRaisesRegex(ValueError, "missing its initialization"):
            render_result.requires_test_code_assessment(self.state, self.run_dir)
        marker.write_text('{"schema_version": 1, "run_id": "v8p2-run-policy-test", '
                          '"policy_version": 2, "digest": "tampered"}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "does not match"):
            render_result.requires_test_code_assessment(self.state, self.run_dir)

    def test_not_needed_requires_allowlisted_reason_category(self) -> None:
        assessment = self.result["test_code_assessment"]
        assessment.update({"outcome": "not-needed", "reviewed_test_paths": [],
                           "test_path_evidence": [], "omission_category": "business-judgment"})
        with self.assertRaisesRegex(ValueError, "allowed omission_category"):
            render_result.validate_test_code_assessment(self.run_dir, self.state, self.result)
        assessment["omission_category"] = "formatting-only"
        render_result.validate_test_code_assessment(self.run_dir, self.state, self.result)

    def test_low_risk_simple_change_can_omit_new_test_code(self) -> None:
        assessment = self.result["test_code_assessment"]
        assessment.update({"outcome": "not-needed", "rationale":
                           "Isolated copy-only text change; no runtime behavior changes; existing checks run.",
                           "reviewed_test_paths": [], "test_path_evidence": [],
                           "omission_category": "low-risk-simple-change"})
        render_result.validate_test_code_assessment(self.run_dir, self.state, self.result)


if __name__ == "__main__":
    unittest.main()
