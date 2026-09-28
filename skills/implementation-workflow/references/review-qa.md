# 코드 검토·QA·최종 보고 단계

1. 구현에 참여하지 않은 읽기 전용 서브에이전트에게 코드 검토를 맡긴다. light는 1명이 설계 일치·정확성·보안·테스트를 확인하고, full은 3명이 이를 나눈다. `review_brief.py`의 짧은 브리프와 시작 커밋 대비 diff·설계·원문 경로를 제공하고 `fork_turns: none`을 우선 사용한다. 구현을 먼저 커밋했다면 v7의 `base_commits` 또는 명시적 `--base <시작-커밋>`으로 커밋된 변경 경로가 브리프에 남는지 확인한다. 공식 출처의 주장과 코드 연결도 해당 역할에 포함한다. 실제 agent ID를 기록한다.
2. v5에서 각 결과의 JSON 원본은 `render_result.py`로 슬롯별 Markdown과 통합 문서를 만든 뒤 `agent-result --stage code-review --result-json --model <actual> --effort <actual> --model-reason <selection-or-fallback> --context-mode <isolated|full-fallback> --context-reason <actual-reason>`에 묶는다. 발견 사항은 해결 근거를 `resolve-agent`에 기록하고 현재 코드에서 세 명 모두 다시 검토한다.
3. 선택한 코드 검토가 CLEAR이면 다른 서브에이전트가 QA를 실제 수행한다. light는 1명이 정상·경계·권한·회귀·운영을 모두 검증하고, full은 3명이 나눈다. `qa-plan.json`의 각 시나리오에 환경·입력·기대/실제·시각·고유 로컬 증거를 남긴다. 실패·미실행은 `resolve-qa-case`와 새 증거로 재수행한다.
4. 두 영역 작업은 full이므로 양쪽 QA 통과 후 연동 QA도 서로 다른 3명이 수행한다. 새 영역 QA가 기록되면 연동 QA를 다시 수행한다. 결함 수정으로 코드가 바뀌면 영향받는 영역과 연동의 검사·검토·QA 게이트를 다시 확인한다.
5. QA 원본을 JSON·Markdown·하네스 이벤트에 묶는다. 집계는 선택한 인원의 결과와 상충 의견을 보존한다. 범위 밖 발견은 질문 ID, 경로, 영향, 사용자 답변 또는 미응답 상태와 함께 기록하며 승인 전에는 구현하지 않는다. `final-report.md` 초안은 변경 파일·코드 위치, 각 동작 원리, 실행한 검사, 실제 검토·QA 인원, 발견·수정, 공식 출처, 사용량 관측과 미검증 범위를 포함한다. 변경 코드 인용은 `area:path:new:N`으로 표시한다. 삭제 줄은 `area:path:old:N (baseline:<40자리 SHA>)`으로 표시해 저장 기준을 밝힌다. 하네스는 인용 줄이 실제 diff hunk에 있는지 확인한다. 하네스의 `report-publish --draft-file <로컬-초안>`로 질문 상태를 닫고 현재 증거에 결합된 최종 보고서를 게시한다. 그 뒤 `check --gate final`과 `status`의 `complete`를 확인한다. 실패하면 `blocker`와 `next_action`에 따라 같은 실행에서 빠진 검증을 수행하고 완료로 표시하지 않는다.

하네스는 파일 해시와 순서를 검증하지만 사용자 응답·에이전트 신원·실제 테스트의 진실성까지 증명하지 못한다. 코디네이터가 증거를 확인한다. 이전 v2~v4 실행은 기존 `workflow.md`·`orchestration.md`의 절차와 명령을 따른다.
