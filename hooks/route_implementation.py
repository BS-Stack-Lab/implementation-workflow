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
                "Load implementation-workflow. For a new implementation request, call the host "
                "request_user_input_async with 2-3 choices and free-text input; fallback to the same "
                "conversation question only if unavailable or failed. accepted=true means delivered, "
                "not answered. Do not end the turn after delivery. Continue independent work; if only "
                "the user's answer blocks progress, repeat clock.sleep waits of at most 60 seconds until "
                "an explicit answer or decline. Silence/timeouts never answer or justify final/incomplete "
                "reports. On actual interruption, resume the same work item and reissue the same pending "
                "question. Reuse saved ID/revision/body/options for run-scoped questions; replay the same "
                "canonical question '설계 문서를 작성한 뒤 어떻게 진행할까요?' with choices "
                "'독립 설계 검토 후 바로 구현 (추천)' and '설계 문서를 직접 검토한 뒤 구현' "
                "for pre-run mode questions. Identify/resume existing runs without "
                "repeating mode choice. Immediate runs continue through implementation, tests, review, QA, "
                "and report after design gate; user-review waits for acceptance. Store documents in "
                "~/Documents/docs outside Git. Use version-matched official docs. Report out-of-scope "
                "items locally without blocking requested work. Policy comments only."
            ),
        }
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
