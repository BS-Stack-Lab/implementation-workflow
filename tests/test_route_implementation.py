"""Verify that implementation prompts carry the review-choice instruction."""

import json
import subprocess
import sys
import unittest
from pathlib import Path


HOOK = Path(__file__).resolve().parents[1] / "hooks" / "route_implementation.py"


class RouteImplementationTest(unittest.TestCase):
    def invoke(self, prompt: str) -> str:
        result = subprocess.run(
            [sys.executable, str(HOOK)],
            input=json.dumps({"hook_event_name": "UserPromptSubmit", "prompt": prompt}),
            text=True, capture_output=True, check=True,
        )
        return result.stdout

    def test_implementation_request_requires_fresh_choice(self) -> None:
        output = json.loads(self.invoke("이 기능 구현해줘"))
        context = output["hookSpecificOutput"]["additionalContext"]
        self.assertLessEqual(len(context), 1200)
        self.assertIn("request_user_input_async", context)
        self.assertIn("2-3 choices and free-text input", context)
        self.assertIn("accepted=true means delivered, not answered", context)
        self.assertIn("Do not end the turn after delivery", context)
        self.assertIn("clock.sleep waits of at most 60 seconds", context)
        self.assertIn("Silence/timeouts never answer", context)
        self.assertIn("final/incomplete reports", context)
        self.assertIn("reissue the same pending question", context)
        self.assertIn("설계 문서를 작성한 뒤 어떻게 진행할까요?", context)
        self.assertIn("독립 설계 검토 후 바로 구현 (추천)", context)
        self.assertIn("설계 문서를 직접 검토한 뒤 구현", context)
        self.assertIn("Use version-matched official docs", context)
        self.assertIn("Report out-of-scope items locally", context)

    def test_explanation_request_does_not_route(self) -> None:
        self.assertEqual(self.invoke("구현 방법을 설명해줘"), "")


if __name__ == "__main__":
    unittest.main()
