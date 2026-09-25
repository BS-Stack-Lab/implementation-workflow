#!/usr/bin/env python3
"""Suggest the implementation workflow only for requests to change code."""

from __future__ import annotations

import json
import sys


ACTION_TERMS = (
    "구현해", "구현해줘", "구현해 줘", "개발해", "개발해줘", "개발해 줘",
    "코드 작성", "코드를 작성", "코드 수정", "코드를 수정", "기능 추가",
    "기능을 추가", "버그 수정", "오류 수정", "고쳐줘", "고쳐 줘",
    "implement", "build the feature", "write code", "change the code",
    "fix the bug", "add the feature", "이어서 진행", "계속 진행",
    "작업 진행", "진행해줘", "continue implementation",
)
DISCUSSION_TERMS = (
    "방법", "설명", "알려줘", "가능한지", "가능해", "비교", "추천",
    "계획만", "설계만", "검토만", "implementing workflow", "how to",
    "explain", "compare", "recommend",
)


def should_route(prompt: str) -> bool:
    text = prompt.casefold()
    document_work_request = (
        any(term in text for term in ("설계 문서", "설계된 문서", "테크스펙", "기능 명세"))
        and any(term in text for term in ("작업해", "작업을 진행", "진행해"))
    )
    if not any(term in text for term in ACTION_TERMS) and not document_work_request:
        return False
    if any(term in text for term in DISCUSSION_TERMS) and not any(
        term in text for term in ("진행해", "수정해", "구현해", "작성해", "만들어")
    ):
        return False
    return True


def main() -> None:
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return
    if not isinstance(event, dict) or event.get("hook_event_name") != "UserPromptSubmit":
        return
    prompt = event.get("prompt")
    if not isinstance(prompt, str) or not should_route(prompt):
        return
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": (
                "Load the implementation-workflow skill. For EVERY new implementation request, "
                "ask the two design review modes using a choice/free-text UI. Wait for an explicit answer. "
                "First identify the work item and existing local run; use harness status to resume "
                "its next_action without repeating the mode question. A design-only request ends "
                "at design delivery. For an immediate-mode implementation run, pass the design gate "
                "and continue through code, tests, review, QA, and final report in this task. "
                "User-review waits for explicit design acceptance. Keep generated documents in "
                "~/Documents/docs outside Git."
            ),
        }
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
