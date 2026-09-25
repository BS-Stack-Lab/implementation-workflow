# 요청·설계·승인 단계

1. 원문 요청의 경로·버전, 프론트엔드·백엔드 각각의 저장소 루트·브랜치·사용자 변경, 실제 코드·검사 명령을 확인한다. 같은 저장소라면 두 영역에 같은 루트를 지정할 수 있다. 작업 영역만 설계한다. 프론트엔드면 `frontend-design.md`, 백엔드면 `backend-design.md`, 둘 다면 두 문서와 API·데이터 계약을 각각 작성한다.
2. 작업 ID에 해당하는 `~/Documents/docs/` 실행 폴더를 먼저 찾고 `workflow_harness.py status --run-dir <run-dir>`로 확인한다. 동일 작업의 미완료 실행이면 저장된 모드·범위·`next_action`에서 재개한다. 다른 저장소, 다른 작업 ID, 완료된 실행이나 모호한 후보를 임의로 이어 붙이지 않는다.
3. **새 구현 요청일 때만** 질문 UI로 독립 검토 후 바로 구현(`immediate`)과 사용자가 설계를 검토한 뒤 구현(`user-review`) 중 하나를 명시적으로 선택받는다. 이 요청의 응답을 `--mode-reference`에 남긴다. 이전 작업의 선택을 재사용하지 않는다. `--work-item`으로 동일 작업의 미완료 실행을 재사용하며, 명시적 새 실행에는 `--new-run`을 사용한다. 생성 문서는 관련 Git 저장소 모두의 밖에 둔다.
4. 위험을 분류해 `--review-depth light|full`을 정한다. 작은 단일 영역만 light이고, 위험·불명확·두 영역은 full이다. `python3 <plugin-root>/scripts/orchestrate.py --scope <frontend|backend|both> --review-mode <immediate|user-review> --review-depth <light|full>`로 그래프를 보고 `scripts/workflow_harness.py init --review-depth <light|full> --risk-reason <근거>`를 실행한다. 두 영역은 `--frontend-repo`·`--backend-repo`를 각각 지정하고 필요하면 `--integration-repo`를 지정한다. 기존 실행에서는 `orchestrate.py --run-dir <run-dir>`로 상태와 그래프를 함께 읽는다.
5. 설계의 `## 수용 기준`에서 R1/AC-1 같은 고유 ID를 한 줄씩 선언한다. 실제 저장소 명령으로 `verification-plan.json`을, 구체적인 입력·절차·기대 결과로 `qa-plan.json`을 작성한다. v6 QA는 `kind`에 `normal`, `boundary`, `authorization`, `regression`, `operations`를 모두 포함하고 모든 기준을 연결한다. light는 slot 1만, full은 1·2·3을 사용한다. 두 영역이면 연동 시나리오에 `source_area`를 지정한다.
6. `user-review`면 설계 초안의 클릭 가능한 절대 경로를 즉시 보여주고 `present`로 기록한다.
7. 설계 작성에 참여하지 않은 **light 1명 또는 full 3명**의 읽기 전용 서브에이전트가 독립 검토한다. `review_brief.py`로 원문 경로·버전, 승인 참조, 계획·수용 기준·변경 경로를 담은 짧은 브리프를 만든다. `fork_turns: none`을 우선 사용한다. 지원하지 않으면 전체 문맥 전환 이유를 기록한다. 실제 agent ID와 슬롯 초점을 기록한다.
8. 구조화 JSON을 `render_result.py`로 슬롯별 Markdown과 통합 문서로 렌더하고 `review --result-json`에 연결한다. 실제 모델·문맥 선택과 이유를 기록한다. 한 명이라도 수정 요구면 모두의 발견 사항을 보고하고 사용자 승인 뒤 `approve`를 기록한다. 승인 전 설계 변경·구현을 하지 않는다. 수정 후 선택한 인원 모두 다시 검토한다.
9. `user-review`면 최종 설계와 검토 링크를 보여주고 사용자 확인을 `accept-design`에 기록한다. `immediate`면 독립 검토가 모두 통과한 뒤 바로 진행한다. `check --gate design` 통과 전에는 구현하지 않는다. 게이트 통과 후 `status`에서 다음 단계를 확인하고, 구현까지 요청된 `immediate` 작업은 구현·검사·코드 검토·QA·최종 보고까지 이어간다. 설계만 요청되었다면 설계 전달에서 마친다.

하네스 명령의 전체 인수는 `python3 <plugin-root>/scripts/workflow_harness.py --help`와 해당 하위 명령 `--help`로 확인한다. v2~v4 실행은 기존 `workflow.md`·`orchestration.md`의 명령을 계속 사용한다.
