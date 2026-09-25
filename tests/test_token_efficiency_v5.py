"""Exercise scoped invalidation, captured checks, and structured result evidence."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from evidence_summary import read_manifest, summarize  # noqa: E402
from orchestrate import choose_model, focus_for  # noqa: E402
from render_result import aggregate, markdown, validate, write_current  # noqa: E402
from review_brief import brief  # noqa: E402
from run_check import capture  # noqa: E402
from workflow_harness import (artifact_digest, code_digest_for, design_digest,  # noqa: E402
                              impact_map, manifest_binding, plan_digests,
                              result_binding, verify_all_evidence)


class TokenEfficiencyV5Test(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        previous_home = os.environ.get("HOME")
        os.environ["HOME"] = str(self.home)
        self.addCleanup(lambda: os.environ.__setitem__("HOME", previous_home)
                        if previous_home is not None else os.environ.pop("HOME", None))
        self.repo = self.home / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "-C", str(self.repo), "init", "-q"], check=True)
        for name in ("frontend/page.py", "backend/api.py", "frontend/contracts/api.json",
                     "contracts/api.json"):
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("initial\n")
        subprocess.run(["git", "-C", str(self.repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.repo), "-c", "core.hooksPath=/dev/null",
                        "-c", "user.name=Test", "-c", "user.email=test@example.test",
                        "commit", "-qm", "initial"], check=True)
        self.run_dir = self.home / "Documents" / "docs" / "run"
        self.run_dir.mkdir(parents=True)
        self.state = {"version": 5, "scope": "both", "repo_root": str(self.repo),
                      "events": []}
        (self.run_dir / "state.json").write_text(json.dumps(self.state))
        self.map = {"frontend": [{"glob": "frontend/**", "reason": "frontend-owned"}],
                    "backend": [{"glob": "backend/**", "reason": "backend-owned"}],
                    "shared": [], "criteria_paths": {}}
        self.write_map()

    def write_map(self) -> None:
        (self.run_dir / "impact-map.json").write_text(json.dumps(self.map))

    def test_scoped_digest_and_independent_shared_paths(self) -> None:
        base = {area: code_digest_for(self.run_dir, self.state, area)
                for area in ("frontend", "backend", "integration")}
        (self.repo / "frontend/page.py").write_text("changed\n")
        first = {area: code_digest_for(self.run_dir, self.state, area)
                 for area in base}
        self.assertNotEqual(first["frontend"], base["frontend"])
        self.assertEqual(first["backend"], base["backend"])
        self.assertNotEqual(first["integration"], base["integration"])
        for name in ("frontend/contracts/api.json", "contracts/api.json"):
            (self.repo / name).write_text("shared changed\n")
            current = {area: code_digest_for(self.run_dir, self.state, area)
                       for area in base}
            self.assertNotEqual(current["backend"], first["backend"])
            first = current

    def test_invalid_map_is_rejected_and_missing_map_is_conservative(self) -> None:
        self.map["frontend"][0]["glob"] = "../frontend/**"
        self.write_map()
        with self.assertRaises(ValueError):
            impact_map(self.run_dir, self.state)
        (self.run_dir / "impact-map.json").unlink()
        base_front = code_digest_for(self.run_dir, self.state, "frontend")
        base_back = code_digest_for(self.run_dir, self.state, "backend")
        self.assertEqual(base_front, base_back)
        (self.repo / "backend/api.py").write_text("changed\n")
        self.assertNotEqual(base_front, code_digest_for(self.run_dir, self.state, "frontend"))

    def test_runner_manifest_rejects_failed_pass_wrong_command_and_tamper(self) -> None:
        (self.run_dir / "backend-design.md").write_text("## 수용 기준\nR1. outcome\n")
        (self.run_dir / "frontend-design.md").write_text("## 수용 기준\nR1. outcome\n")
        checks = {area: [{"id": "check", "kind": "test", "command": "python3 -c 'exit(0)'",
                          "required": True}] for area in ("frontend", "backend", "integration")}
        cases = {area: [{"slot": slot, "scenario_id": f"s{slot}", "criterion_id": "R1",
                         "source_area": "backend" if area == "integration" else area,
                         "steps": "run", "expected": "pass"} for slot in (1, 2, 3)]
                 for area in checks}
        (self.run_dir / "verification-plan.json").write_text(json.dumps(checks))
        (self.run_dir / "qa-plan.json").write_text(json.dumps(cases))
        failed = capture(self.run_dir, self.repo, "python3 -c 'exit(1)'", area="backend")
        with self.assertRaises(ValueError):
            manifest_binding(self.run_dir, self.state, "backend", "check", str(failed), "pass")
        with self.assertRaises(ValueError):
            manifest_binding(self.run_dir, self.state, "backend", "check", str(failed), "fail")
        passed = capture(self.run_dir, self.repo, "python3 -c 'exit(0)'", area="backend")
        binding = manifest_binding(self.run_dir, self.state, "backend", "check", str(passed), "pass")
        self.assertEqual(binding["attempt_id"], read_manifest(self.run_dir, str(passed))[0]["attempt_id"])
        summary = summarize(self.run_dir, str(passed))
        self.assertEqual(summary["status"], "pass")
        log = self.run_dir / binding["evidence_file"]
        log.write_text("tampered\n")
        with self.assertRaises(ValueError):
            read_manifest(self.run_dir, str(passed))

    def test_manifest_rejects_wrong_checkout_and_stale_code(self) -> None:
        with self.assertRaises(ValueError):
            capture(self.run_dir, self.home, "true", area="backend")
        manifest = capture(self.run_dir, self.repo, "true", area="backend")
        (self.repo / "backend/api.py").write_text("edited after check\n")
        with self.assertRaisesRegex(ValueError, "another repository, area, or code state"):
            manifest_binding(self.run_dir, self.state, "backend", "check", str(manifest),
                             "pass", expected_command="true")

    def test_historical_manifest_survives_plan_command_change(self) -> None:
        self.state["scope"] = "backend"
        (self.run_dir / "state.json").write_text(json.dumps(self.state))
        (self.run_dir / "backend-design.md").write_text("## 수용 기준\nR1. expected\n")
        (self.run_dir / "verification-plan.json").write_text(json.dumps({"backend": [
            {"id": "tests", "kind": "test", "command": "true", "required": True}]}))
        (self.run_dir / "qa-plan.json").write_text(json.dumps({"backend": [
            {"slot": slot, "scenario_id": f"s{slot}", "criterion_id": "R1",
             "steps": "run", "expected": "pass"} for slot in (1, 2, 3)]}))
        manifest = capture(self.run_dir, self.repo, "true")
        bound = manifest_binding(self.run_dir, self.state, "backend", "tests", str(manifest), "pass")
        event = {"type": "check", "area": "backend", "name": "tests", "status": "pass",
                 "digest": code_digest_for(self.run_dir, self.state, "backend"),
                 "design_digest": design_digest(self.run_dir, "backend"),
                 "plans": plan_digests(self.run_dir), "planned_command": "true", **bound}
        self.state["events"] = [event]
        (self.run_dir / "verification-plan.json").write_text(json.dumps({"backend": [
            {"id": "tests", "kind": "test", "command": "python3 -c 'exit(0)'",
             "required": True}]}))
        verify_all_evidence(self.run_dir, self.state)
        (self.run_dir / bound["evidence_file"]).write_text("tampered\n")
        with self.assertRaises(ValueError):
            verify_all_evidence(self.run_dir, self.state)

    def test_genuine_v4_state_shape_and_legacy_check_cli(self) -> None:
        run = self.home / "Documents" / "docs" / "legacy-v4"
        run.mkdir()
        (run / "frontend-design.md").write_text("## 수용 기준\nR1. expected\n")
        (run / "frontend-design-review.md").write_text("Three reviews\n")
        (run / "verification-plan.json").write_text(json.dumps({"frontend": [
            {"id": "tests", "kind": "test", "command": "true", "required": True}]}))
        (run / "qa-plan.json").write_text(json.dumps({"frontend": [
            {"slot": slot, "scenario_id": f"s{slot}", "criterion_id": "R1",
             "steps": "run", "expected": "pass"} for slot in (1, 2, 3)]}))
        state = {"version": 4, "repo_root": str(self.repo), "scope": "frontend",
                 "review_mode": "immediate", "mode_reference": "historical answer", "events": []}
        hashes = plan_digests(run)
        for slot in (1, 2, 3):
            name = f"frontend-design-review-{slot}.md"
            (run / name).write_text(f"review {slot}\n")
            state["events"].append({"type": "review", "area": "frontend", "slot": slot,
                                    "agent_id": f"/root/old-{slot}", "result": "clear",
                                    "finding": "", "digest": design_digest(run, "frontend"),
                                    "artifact_digest": artifact_digest(run, name),
                                    "focus": focus_for("design-review", "frontend", slot),
                                    "plans": hashes})
        (run / "state.json").write_text(json.dumps(state))
        harness = ROOT / "scripts" / "workflow_harness.py"
        result = subprocess.run([sys.executable, str(harness), "check", "--run-dir", str(run),
                                 "--gate", "design"], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        evidence = run / "old-check.txt"
        evidence.write_text("old test passed\n")
        result = subprocess.run([sys.executable, str(harness), "check-record", "--run-dir",
                                 str(run), "--area", "frontend", "--name", "tests",
                                 "--status", "pass", "--actual", "pass",
                                 "--evidence-file", str(evidence)], text=True,
                                capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_structured_result_binding_and_model_fallback(self) -> None:
        data = {"schema_version": 1, "stage": "code-review", "area": "backend",
                "slot": 1, "agent_id": "/root/reviewer-1",
                "focus": focus_for("code-review", "backend", 1), "result": "clear",
                "findings": [], "evidence": []}
        original = self.run_dir / "result-1.json"
        original.write_text(json.dumps(data))
        parsed, _, _ = validate(self.run_dir, str(original))
        name = "backend-code-review-1.md"
        write_current(self.run_dir, name, markdown(parsed))
        bound = result_binding(self.run_dir, str(original), "code-review", "backend", 1,
                               "/root/reviewer-1", data["focus"], "clear")
        self.assertEqual(bound["result_json_digest"], hashlib.sha256(original.read_bytes()).hexdigest())
        data["findings"] = [{"id": "F1", "location": "file:1", "observation": "bad",
                             "impact": "wrong", "correction": "fix", "reproduction": "run"}]
        original.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            validate(self.run_dir, str(original))
        selection = choose_model("qa", 2, {"gpt-6-sol": ["low"],
                                            "gpt-6-luna": ["medium"]},
                                 "gpt-6-luna", "medium")
        self.assertEqual((selection["model"], selection["effort"]), ("gpt-6-luna", "medium"))
        fallback = choose_model("qa", 2, {"gpt-6-sol": ["low"]}, "gpt-6-sol", "low")
        self.assertEqual((fallback["model"], fallback["effort"]), ("gpt-6-sol", "low"))

    def test_brief_links_source_and_keeps_stage_policies(self) -> None:
        self.state["review_mode"] = "immediate"
        (self.run_dir / "state.json").write_text(json.dumps(self.state))
        for area in ("frontend", "backend"):
            (self.run_dir / f"{area}-design.md").write_text("## 수용 기준\nR1. expected\n")
        output = brief(self.run_dir, "design-review", "backend", 1,
                       "conversation:request-42", "v1", "none",
                       {"gpt-6-luna": ["medium"]}, "gpt-6-luna", "medium")
        self.assertEqual(output["spawn"]["preferred"],
                         {"fork_turns": "none", "context_mode": "isolated"})
        self.assertEqual(output["spawn"]["fallback"]["context_mode"], "full-fallback")
        self.assertEqual(output["source"], "conversation:request-42")
        self.assertEqual(output["acceptance_ids"], {"backend": ["R1"]})
        self.assertEqual(output["focus"], "requirements")
        (self.run_dir / "finding.json").write_text(json.dumps({"findings": [
            {"id": "C1", "observation": "missing bound"}]}))
        self.state["events"] = [
            {"type": "code-review", "area": "backend", "slot": 2,
             "result": "findings", "result_json": "finding.json", "digest": "old"},
            {"type": "qa-case", "area": "backend", "scenario_id": "s2",
             "result": "fail", "actual": "unexpected", "digest": "old"},
        ]
        (self.run_dir / "state.json").write_text(json.dumps(self.state))
        output = brief(self.run_dir, "design-review", "backend", 1,
                       "conversation:request-42", "v1", "none",
                       {"gpt-6-luna": ["medium"]}, "gpt-6-luna", "medium")
        self.assertEqual({item["id"] for item in output["open_findings"]}, {"C1", "s2"})
        skill = (ROOT / "skills" / "implementation-workflow" / "SKILL.md").read_text()
        for policy in ("질문 UI", "설계 변경 승인", "설계 검토 1명", "서로 다른 3명", "~/Documents/docs/"):
            self.assertIn(policy, skill)
        for name in ("intake-design.md", "implementation-check.md", "review-qa.md"):
            self.assertIn(name, skill)

    def test_aggregate_preserves_slot_disagreement_and_archives_previous(self) -> None:
        items = []
        for slot in (1, 2, 3):
            findings = ([{"id": "F1", "location": "file:2", "observation": "unsafe path",
                          "impact": "wrong output", "correction": "validate",
                          "reproduction": "run invalid input"}] if slot == 2 else [])
            items.append({"schema_version": 1, "stage": "code-review", "area": "backend",
                          "slot": slot, "agent_id": f"/root/reviewer-{slot}",
                          "focus": focus_for("code-review", "backend", slot),
                          "result": "findings" if findings else "clear",
                          "findings": findings, "evidence": []})
        rendered = aggregate(items)
        self.assertIn("F1", rendered)
        self.assertIn("**clear**", rendered)
        self.assertIn("**findings**", rendered)
        first = write_current(self.run_dir, "backend-code-review.md", rendered)
        write_current(self.run_dir, "backend-code-review.md", "# revised\n")
        archives = list(self.run_dir.glob("backend-code-review-attempt-*.md"))
        self.assertEqual(len(archives), 1)
        self.assertEqual(archives[0].read_text(), rendered)
        self.assertEqual(first.read_text(), "# revised\n")

    def test_v5_cli_final_gate_and_manifest_tamper(self) -> None:
        run = self.home / "Documents" / "docs" / "cli-run"

        def call(*args: str, fails: bool = False) -> None:
            result = subprocess.run([sys.executable, str(ROOT / "scripts" / "workflow_harness.py"),
                                     *args], text=True, capture_output=True)
            if fails:
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            else:
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        def command(name: str, *args: str, fails: bool = False) -> None:
            call(name, "--run-dir", str(run), *args, fails=fails)

        def result_json(stage: str, slot: int, result: str) -> Path:
            data = {"schema_version": 1, "stage": stage, "area": "backend",
                    "slot": slot, "agent_id": f"/root/{stage}-{slot}",
                    "focus": focus_for(stage, "backend", slot), "result": result,
                    "findings": [], "evidence": []}
            path = run / f"{stage}-{slot}.json"
            path.write_text(json.dumps(data))
            subprocess.run([sys.executable, str(ROOT / "scripts" / "render_result.py"),
                            "--run-dir", str(run), "--input-json", str(path)], check=True,
                           capture_output=True)
            return path

        call("init", "--repo", str(self.repo), "--scope", "backend",
             "--review-mode", "immediate", "--mode-reference", "user chose immediate",
             "--run-dir", str(run))
        state = json.loads((run / "state.json").read_text())
        self.assertEqual(state["version"], 7)
        state["version"] = 5
        (run / "state.json").write_text(json.dumps(state))
        (run / "backend-design.md").write_text("## 수용 기준\nR1. expected\n")
        (run / "verification-plan.json").write_text(json.dumps({"backend": [
            {"id": "tests", "kind": "test", "command": "python3 -c 'exit(0)'", "required": True}]}))
        (run / "qa-plan.json").write_text(json.dumps({"backend": [
            {"slot": slot, "scenario_id": f"s{slot}", "criterion_id": "R1",
             "steps": "execute", "expected": "pass"} for slot in (1, 2, 3)]}))
        (run / "backend-design-review.md").write_text("Three independent reviews\n")
        for slot in (1, 2, 3):
            path = result_json("design-review", slot, "clear")
            command("review", "--area", "backend", "--slot", str(slot),
                    "--agent-id", f"/root/design-review-{slot}",
                    "--focus", focus_for("design-review", "backend", slot),
                    "--result", "clear", "--result-json", str(path),
                    "--model", "gpt-6-luna", "--effort", "medium",
                    "--model-reason", "recommended", "--context-mode", "isolated",
                    "--context-reason", "fork_turns none")
        subprocess.run([sys.executable, str(ROOT / "scripts" / "render_result.py"),
                        "--run-dir", str(run), "--aggregate", *[
                            part for slot in (1, 2, 3)
                            for part in ("--input-json", str(run / f"design-review-{slot}.json"))]],
                       check=True, capture_output=True)
        self.assertTrue(list(run.glob("backend-design-review-attempt-*.md")))
        command("check", "--gate", "design")
        manifest = capture(run, self.repo, "python3 -c 'exit(0)'")
        command("check-record", "--area", "backend", "--name", "tests",
                "--status", "pass", "--manifest-file", str(manifest))
        command("check-record", "--area", "backend", "--name", "tests",
                "--status", "pass", "--manifest-file", str(manifest), fails=True)
        (run / "backend-code-review.md").write_text("Three independent reviews\n")
        for slot in (1, 2, 3):
            path = result_json("code-review", slot, "clear")
            command("agent-result", "--stage", "code-review", "--area", "backend",
                    "--slot", str(slot), "--agent-id", f"/root/code-review-{slot}",
                    "--focus", focus_for("code-review", "backend", slot),
                    "--result", "clear", "--result-json", str(path),
                    "--model", "gpt-6-luna", "--effort", "medium",
                    "--model-reason", "recommended", "--context-mode", "isolated",
                    "--context-reason", "fork_turns none")
        (run / "backend-qa.md").write_text("Three QA agents\n")
        for slot in (1, 2, 3):
            evidence = run / f"qa-{slot}.txt"
            evidence.write_text("actual pass\n")
            command("qa-case", "--area", "backend", "--slot", str(slot),
                    "--agent-id", f"/root/qa-{slot}", "--scenario-id", f"s{slot}",
                    "--result", "pass", "--environment", "local fixture",
                    "--input", "fixture", "--actual", "pass", "--evidence-file", str(evidence))
            path = result_json("qa", slot, "pass")
            command("agent-result", "--stage", "qa", "--area", "backend",
                    "--slot", str(slot), "--agent-id", f"/root/qa-{slot}",
                    "--focus", focus_for("qa", "backend", slot),
                    "--result", "pass", "--result-json", str(path),
                    "--model", "gpt-6-luna", "--effort", "medium",
                    "--model-reason", "fallback: gpt-6-sol unavailable",
                    "--context-mode", "full-fallback",
                    "--context-reason", "host did not support fork_turns none")
        (run / "final-report.md").write_text("All checks and QA passed\n")
        command("check", "--gate", "final")
        state = json.loads((run / "state.json").read_text())
        self.assertEqual(state["events"][-1]["model_reason"],
                         "fallback: gpt-6-sol unavailable")
        self.assertEqual(state["events"][-1]["context_mode"], "full-fallback")
        original = manifest.read_bytes()
        manifest.write_text("tampered\n")
        command("check", "--gate", "final", fails=True)
        manifest.write_bytes(original)
        command("check", "--gate", "final")


if __name__ == "__main__":
    unittest.main()
