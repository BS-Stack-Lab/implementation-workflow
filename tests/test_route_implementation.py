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
        self.assertIn("EVERY new implementation request", context)
        self.assertIn("Wait for an explicit answer", context)

    def test_explanation_request_does_not_route(self) -> None:
        self.assertEqual(self.invoke("구현 방법을 설명해줘"), "")


if __name__ == "__main__":
    unittest.main()
