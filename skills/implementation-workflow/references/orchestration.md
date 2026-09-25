# 서브에이전트 운영과 하네스

## 현재 실행 재개

새 작업을 시작하기 전에 `~/Documents/docs/`에서 같은 작업 ID의 실행 폴더를 찾는다. `python3 <plugin-root>/scripts/workflow_harness.py status --run-dir <local-run-dir>`로 `stage`, `next_action`, `blocker`, `complete`를 확인한다. 미완료 실행이면 `python3 <plugin-root>/scripts/orchestrate.py --run-dir <local-run-dir>`의 상태와 그래프를 사용해 다음 단계부터 계속한다. 설계 게이트를 통과한 `immediate` 구현 작업을 설계 문서 전달만으로 종료하지 않는다. `user-review`의 설계 확인이나 추가 설계 변경 승인처럼 실제 사용자 응답이 필요한 경우에는 그 응답을 기다린다. 설계만 요청된 작업은 설계 문서를 전달하면 범위가 끝난다.

신규 실행에서는 `--work-item <작업-ID>`를 지정해 동일 작업의 미완료 실행을 재사용한다. 명시적으로 새 실행을 만들 때는 `--new-run`을 사용한다. 두 영역이 다른 Git 체크아웃에 있으면 `init --scope both --frontend-repo <FE-저장소> --backend-repo <BE-저장소>`를 사용한다. 연동 명령의 작업 디렉터리를 별도로 지정하려면 `--integration-repo <저장소>`를 추가한다. 같은 체크아웃에서 양쪽을 작업하면 두 인수에 같은 루트를 지정한다. 기존 실행 기록은 해당 버전의 방식으로 읽는다.

## 범위별 작업 그래프

플러그인 루트는 이 `SKILL.md` 파일의 두 단계 위 디렉터리다. 범위를 판별한 다음 다음 명령으로 작업 그래프를 읽는다.

```sh
python3 <plugin-root>/scripts/orchestrate.py --scope frontend --review-mode <immediate|user-review>
python3 <plugin-root>/scripts/orchestrate.py --scope backend --review-mode <immediate|user-review>
python3 <plugin-root>/scripts/orchestrate.py --scope both --review-mode <immediate|user-review>
```

매 새로운 구현 요청마다 사용자에게 검토 방식을 물은 뒤 실제 범위에 해당하는 명령 한 가지만 실행한다. 이전 선택을 재사용하지 않는다. `immediate`는 독립 검토 후 진행하고, `user-review`는 작성된 설계를 사용자에게 보여주고 최종 설계 확인을 기다린다. 그래프의 `depends_on`을 지킨다. 각 설계 검토·코드 검토·QA 단계는 영역마다 번호가 붙은 세 작업으로 구성된다. `both`일 때 계약 검토와 연동 QA도 각각 세 작업이다. 실행 환경의 동시 슬롯이 부족하면 세 작업을 순차로 배정하되 서로 다른 실제 서브에이전트가 담당해야 한다. 스크립트는 계획을 출력하며 에이전트를 직접 생성하지 않는다. 코디네이터가 사용 가능한 Codex 서브에이전트 도구로 역할을 배정하고 결과를 수집한다.

## 역할과 결과

- 코디네이터: 요구사항과 실제 변경 영역을 판별하고 설계·검토·승인·검증의 증거를 로컬 작업 폴더에 기록한다. 공유 파일과 범위 변경을 관리한다.
- 영역별 설계자: 해당 영역의 설계 문서만 작성한다. 두 영역이 모두 필요하면 API·데이터 계약을 양쪽 문서에서 일치시킨다.
- 영역별 설계 검토자 3명: 설계 작성에 참여하지 않은 읽기 전용 서브에이전트다. 슬롯 1은 요구사항·수용 기준, 2는 구조·호환성, 3은 오류·보안·검증 가능성을 맡는다. 각자 위치·영향·수정안을 원본 문서에 보고한다.
- 계약 검토자 3명: 두 영역이 관련될 때만 양쪽 설계의 요청·응답·오류·인증·호환성을 독립적으로 대조한다.
- 영역별 구현자: 승인 게이트 뒤 지정된 파일 소유 범위만 수정한다. 공유 파일은 코디네이터가 순서를 정한다.
- 영역별 코드 검토자 3명: 해당 구현에 참여하지 않은 읽기 전용 서브에이전트다. 슬롯 1은 설계 일치·회귀, 2는 정확성·보안, 3은 테스트·유지보수를 맡는다.
- 영역별 QA 담당자 3명: 해당 구현에 참여하지 않은 서로 다른 서브에이전트다. 슬롯 1은 정상 흐름, 2는 경계·오류·권한, 3은 회귀·운영 환경을 맡아 계획된 시나리오를 실제 실행한다. 양쪽이 관련될 때 연동 QA도 3명이 서로 다른 관점으로 수행한다.

서브에이전트에게는 역할·작업 범위·읽을 문서·오케스트레이터가 출력한 슬롯별 `focus`와 질문·기대 산출물·편집 권한을 명시한다. `focus` 값은 결과 기록에도 그대로 사용한다. 검토 역할은 파일을 수정하지 않는다. 각 단계의 실제 canonical agent ID를 기록하고 동일 단계·영역의 세 ID가 다른지 확인한다. 세 명을 확보하지 못하면 완료로 표시하지 않고 이유를 사용자에게 보고한다. 서브에이전트의 판단을 사용자 승인으로 취급하지 않는다. 코디네이터는 세 결론·증거·상충 의견·미해결 항목과 원본 로컬 링크를 통합 리포트에 남긴다. 하네스는 agent ID 문자열의 실제 신원을 독립적으로 증명할 수 없다.

## 신규 실행 계획

신규 v7 실행에서는 설계 검토 결과를 기록하기 전에 실행 폴더에 `verification-plan.json`과 `qa-plan.json`을 작성한다. 저장소에서 확인한 실제 명령만 계획에 넣는다. 전자는 영역마다 고유 검사 ID, 종류(`test`·`lint`·`build`·`other`), 명령, 필수 여부를 담는다. 각 작업 영역에는 필수 `test`가 적어도 하나 필요하며 선택 검사에는 이유를 쓴다. 예를 들어 백엔드만 작업한다면 다음 형태다.

```json
{"backend":[{"id":"unit-tests","kind":"test","command":"python3 -m unittest discover -s tests","required":true}]}
```

`qa-plan.json`은 정상·경계·권한·회귀·운영 다섯 `kind`를 모두 포함하고 영역·슬롯마다 고유 `scenario_id`, 해당 설계의 `## 수용 기준`에 선언한 `criterion_id`, 절차와 기대 결과를 담는다. 모든 적용 기준에 해당 영역의 QA 시나리오를 하나 이상 연결한다. 두 영역의 연동 QA 시나리오에는 기준이 속한 `source_area`도 지정한다.

```json
{"backend":[{"slot":1,"scenario_id":"R1-happy","criterion_id":"R1","kind":"normal","steps":"요청을 실행한다","expected":"정의된 응답을 받는다"},{"slot":2,"scenario_id":"R1-error","criterion_id":"R1","kind":"boundary","steps":"잘못된 요청을 실행한다","expected":"정의된 오류를 받는다"},{"slot":3,"scenario_id":"R1-regression","criterion_id":"R1","kind":"regression","steps":"관련 기존 흐름을 재실행한다","expected":"기존 동작이 유지된다"},{"slot":2,"scenario_id":"R1-auth","criterion_id":"R1","kind":"authorization","steps":"권한 없는 요청을 실행한다","expected":"거부된다"},{"slot":3,"scenario_id":"R1-ops","criterion_id":"R1","kind":"operations","steps":"재시작 뒤 요청을 실행한다","expected":"정상 동작한다"}]}
```

계획과 설계 문서는 실행 폴더 안에 둔다. `design` 게이트는 계획의 범위·중복·수용 기준 커버리지를 검사하고 설계 및 계획 파일의 해시를 기록한다. 설계나 계획이 바뀌면 이전 게이트는 무효다. 원문 요구사항 자체가 설계에서 빠졌는지는 하네스가 알 수 없으므로 설계 검토자와 코디네이터가 대조한다.

## 로컬 하네스 게이트

```sh
python3 <plugin-root>/scripts/workflow_harness.py init --scope <frontend|backend|both> --review-mode <immediate|user-review> --mode-reference <user-answer-for-this-request> [--repo <single-area-repo> | --frontend-repo <FE-repo> --backend-repo <BE-repo> [--integration-repo <integration-repo>]] [--work-item <stable-task-name> | --parent-dir <existing-docs-folder> | --run-dir <new-docs-run-folder>] [--new-run]
python3 <plugin-root>/scripts/workflow_harness.py status --run-dir <local-run-dir>
python3 <plugin-root>/scripts/workflow_harness.py present --run-dir <local-run-dir> --area <frontend|backend>
python3 <plugin-root>/scripts/workflow_harness.py review --run-dir <local-run-dir> --area <area> --slot <1|2|3> --agent-id <actual-subagent-id> --focus <orchestrator-focus> --result <clear|changes-required> [--finding <summary>]
python3 <plugin-root>/scripts/workflow_harness.py approve --run-dir <local-run-dir> --area <area> --reference <actual-user-approval>
python3 <plugin-root>/scripts/workflow_harness.py accept-design --run-dir <local-run-dir> --reference <actual-user-confirmation>
python3 <plugin-root>/scripts/workflow_harness.py check --run-dir <local-run-dir> --gate design
python3 <plugin-root>/scripts/workflow_harness.py check-record --run-dir <local-run-dir> --area <area> --name <planned-check-id> --status <pass|fail> --manifest-file <run-check-manifest>
python3 <plugin-root>/scripts/workflow_harness.py check-record --run-dir <local-run-dir> --area <area> --name <optional-check-id> --status not-run --reason <why> --unverified <affected-scope>
python3 <plugin-root>/scripts/workflow_harness.py resolve-check --run-dir <local-run-dir> --area <area> --name <failed-check-id> --reference <cause-and-action-evidence>
python3 <plugin-root>/scripts/workflow_harness.py agent-result --run-dir <local-run-dir> --stage code-review --area <frontend|backend> --slot <1|2|3> --agent-id <actual-subagent-id> --focus <orchestrator-focus> --result <clear|findings>
python3 <plugin-root>/scripts/workflow_harness.py qa-case --run-dir <local-run-dir> --area <area> --slot <1|2|3> --agent-id <actual-subagent-id> --scenario-id <planned-scenario-id> --result <pass|fail> --environment <environment> --input <input-data> --actual <observed-result> --evidence-file <unique-local-evidence-file>
python3 <plugin-root>/scripts/workflow_harness.py resolve-qa-case --run-dir <local-run-dir> --area <area> --slot <1|2|3> --scenario-id <failed-scenario-id> --reference <cause-and-action-evidence>
python3 <plugin-root>/scripts/workflow_harness.py agent-result --run-dir <local-run-dir> --stage qa --area <area> --slot <1|2|3> --agent-id <actual-subagent-id> --focus <orchestrator-focus> --result pass
python3 <plugin-root>/scripts/workflow_harness.py resolve-agent --run-dir <local-run-dir> --stage code-review --area <frontend|backend> --slot <1|2|3> --reference <resolution-evidence>
python3 <plugin-root>/scripts/workflow_harness.py check --run-dir <local-run-dir> --gate final
```

`init`의 `--mode-reference`에는 이번 구현 요청에 대해 사용자가 두 방식 중 하나를 선택한 실제 응답을 기록한다. 하네스는 빈 참조를 거부하지만 그 응답의 진위를 독립적으로 확인하지는 못한다. `init`이 출력한 작업 폴더에 설계·검토·QA 리포트를 작성한다. `user-review`를 선택했다면 설계 초안 작성 직후 해당 문서의 클릭 가능한 절대 경로를 사용자에게 전달하고 `present`를 기록한다. 문서를 변경하면 다시 전달하고 `present`를 갱신한다. 설계 검토 원본은 `{area}-design-review-1.md`부터 `-3.md`까지 작성하고, 두 영역 작업의 계약 원본은 `integration-contract-review-1.md`부터 `-3.md`까지 작성한다. 세 결과를 모두 수집한 후 한 명이라도 `changes-required`이면 발견 사항 전체를 보고하고 실제 사용자 승인 뒤에만 `approve`를 호출한다. 설계 변경 후 세 명 모두 다시 검토한다. 영역별 통합 설계 검토 문서도 작성한다. `user-review`에서는 최종 설계와 검토 결과를 보여준 뒤 사용자 확인을 받았을 때만 `accept-design`을 기록한다.

`design` 게이트는 현재 설계 버전의 세 개별 결과·서로 다른 agent ID·슬롯별 `focus`, 원본 문서와 계획의 변경 여부, 발견 사항의 승인 순서를 확인한다. `user-review`에서는 현재 설계를 보여준 기록과 동일한 버전의 사용자 확인도 요구한다. 이 게이트가 통과하기 전에는 신규 실행의 검증 결과를 기록할 수 없다. 코드 검토 원본은 `{area}-code-review-1.md`부터 `-3.md`, QA 원본은 `{area}-qa-1.md`부터 `-3.md`다. 필수 검사가 모두 PASS여야 코드 검토를 기록하고, 세 코드 검토가 모두 CLEAR여야 QA 시나리오를 기록한다. 연동 QA는 연동 영역의 필수 검사와 양쪽 영역의 QA가 통과해야 기록한다. 각 QA 담당자의 최종 `agent-result pass`는 담당 시나리오가 모두 PASS일 때만 기록한다. 코디네이터는 별도 통합 문서도 작성한다.

`final` 게이트는 현재 설계·계획·Git 코드 상태와 세 코드 검토, 세 QA, 각 계획 시나리오의 최신 결과, 모든 적용 수용 기준의 커버리지, 최종 리포트를 확인한다. 모든 과거 자동 검증·QA 시도의 증거 파일은 실행 폴더 아래 비어 있지 않은 일반 파일이어야 하고, 시도마다 고유하며 원래 해시와 일치해야 한다. 심볼릭 링크와 실행 폴더 밖 경로는 사용할 수 없다. 두 영역이면 연동 QA 원본 세 건과 통합 문서도 필요하다. 같은 코드 상태에서 코드 검토 `findings`를 해결해 다시 통과시키려면 `resolve-agent`에 해결 근거를 기록한 다음 결과를 다시 기록한다. 검사나 QA 시나리오 실패·미실행 뒤에는 각각 `resolve-check` 또는 `resolve-qa-case`로 원인·조치를 기록하고 같은 ID를 새 증거로 재실행한다. 환경 복구나 설명만으로 PASS로 바꾸지 않는다. 코드나 설계가 바뀌면 관련 검사·세 검토·QA를 새 상태에서 다시 수행한다. 필수 미실행 및 QA `not-run`은 완료가 아니다. 기록된 agent ID, 사용자 응답, 증거 내용의 진실성은 하네스가 독립적으로 증명할 수 없으므로 코디네이터가 실제 결과를 확인한다.

사용자 질문에는 해당 질문 유형에 허용된 Codex 질문 UI를 우선 사용해 선택지와 자유 입력을 제공한다. UI가 없으면 일반 대화로 질문한다. 정확한 버튼 이름은 Codex 클라이언트가 제어한다. 기존 v2·v3 작업 폴더는 이전 CLI와 게이트로 계속 확인하며, 위 manifest 기반 증거 명령은 신규 v7 실행에 적용한다. v2~v6 실행은 당시 상태 버전의 검증 규칙으로 읽는다.
