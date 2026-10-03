# 서브에이전트 운영과 하네스

## 현재 실행 재개

새 작업을 시작하기 전에 `~/Documents/docs/`에서 같은 작업 ID의 실행 폴더를 찾는다. policy-v4 미완료 실행이면 `scope-register.json`을 먼저 읽고 pending 질문을 같은 ID·revision으로 복원한 다음 `python3 <plugin-root>/scripts/workflow_harness.py status --run-dir <local-run-dir>`로 `stage`, `next_action`, `blocker`, `complete`를 확인한다. v2-v3은 기존 status 우선 재개 절차를 유지한다. 미완료 실행이면 `python3 <plugin-root>/scripts/orchestrate.py --run-dir <local-run-dir>`의 상태와 그래프를 사용해 다음 단계부터 계속한다. 설계 게이트를 통과한 `immediate` 구현 작업을 설계 문서 전달만으로 종료하지 않는다. `user-review`의 설계 확인이나 추가 설계 변경 승인처럼 실제 사용자 응답이 필요한 경우에는 그 응답을 기다린다. 설계만 요청된 작업은 설계 문서를 전달하면 범위가 끝난다.

신규 실행에서는 `--work-item <작업-ID>`를 지정해 동일 작업의 미완료 실행을 재사용한다. 명시적으로 새 실행을 만들 때는 `--new-run`을 사용한다. 두 영역이 다른 Git 체크아웃에 있으면 `init --scope both --frontend-repo <FE-저장소> --backend-repo <BE-저장소>`를 사용한다. 연동 명령의 작업 디렉터리를 별도로 지정하려면 `--integration-repo <저장소>`를 추가한다. 같은 체크아웃에서 양쪽을 작업하면 두 인수에 같은 루트를 지정한다. 기존 실행 기록은 해당 버전의 방식으로 읽는다.

불완전한 v2~v7 실행을 정확히 선택해 재개하면 원본 상태를 `legacy-state-vN.json`으로 보존하고 v8 현재 게이트로 승격한다. 이전 run ID와 파일은 유지하지만 공식 출처·범위 질문·현재 계획 digest에 결합되지 않은 이전 PASS를 새 게이트의 증거로 간주하지 않는다. 필요한 단계의 증거를 현재 코드 상태에서 다시 만든다. 완료된 구형 실행은 과거 기록으로 유지한다. 둘 이상의 실행이 일치하면 자동 선택하지 않고 정확한 `--resume-run <run-dir>` 선택을 요구한다. 구형 state에 시작 기준 커밋이 없거나 찾을 수 없으면 `init --resume-run <run-dir> --legacy-base backend=<확인한-커밋>`을 요구한다. `both` 범위는 저장소별로 이 인자를 반복한다. 기준은 현재 checkout의 조상 커밋이어야 한다.

light에서 medium 이상, balanced에서 high/unknown으로 위험이 상향되면 필수 계획을 상속한 새 `full` run으로 재개한다. 실행 잠금·복구 journal을 보장하도록 superseding run과 이전 run은 같은 `~/Documents/docs/` 작업 폴더에 둔다.

## 범위별 작업 그래프

플러그인 루트는 이 `SKILL.md` 파일의 두 단계 위 디렉터리다. 범위를 판별한 다음 다음 명령으로 작업 그래프를 읽는다.

```sh
python3 <plugin-root>/scripts/orchestrate.py --scope frontend --review-mode <immediate|user-review>
python3 <plugin-root>/scripts/orchestrate.py --scope backend --review-mode <immediate|user-review>
python3 <plugin-root>/scripts/orchestrate.py --scope both --review-mode <immediate|user-review>
```

매 새로운 구현 요청마다 사용자에게 검토 방식을 물은 뒤 실제 범위에 해당하는 명령 한 가지만 실행한다. 이전 선택을 재사용하지 않는다. `immediate`는 독립 검토 후 진행하고, `user-review`는 작성된 설계를 사용자에게 보여주고 최종 설계 확인을 기다린다. 그래프의 `depends_on`을 지킨다. `light`는 low-risk 단일 영역, `balanced`는 medium 크기의 단일 영역 또는 경로가 분리된 low/medium-risk 양 영역으로 각 설계·코드 검토·QA·계약/연동 담당을 하나씩 배정하고 한 담당자가 자기 영역의 모든 기준·사례를 확인한다. `full`은 각 역할에 서로 다른 세 에이전트를 배정한다. `--include-waves`가 있을 때만 버전 필드와 함께 결정적 `parallel_waves`를 출력한다. wave 내 작업은 큐에 넣되 실제 실행은 가용 슬롯까지만 시작하고, 작업이 끝날 때마다 같은 wave의 대기 항목으로 슬롯을 채운다. 해당 wave가 모두 끝나야 다음 wave로 이동한다. 같은 Git checkout을 쓰는 FE/BE 구현과 그 검토는 경로 선언과 무관하게 직렬화한다. 서로 다른 checkout의 작업만 run-local exact path ownership map이 경로를 단일 영역에 배정하고 공유·겹침·누락·예상 밖 경로가 없을 때 병렬화한다. 실행 후에는 tracked·untracked·삭제·rename 양쪽 경로를 검사하고 계획과 다르면 직렬 통합·재검증한다. 스크립트는 계획을 출력하며 에이전트를 직접 생성하지 않는다. 코디네이터가 가용 슬롯 수에 맞춰 Codex 서브에이전트를 배정하고 wave barrier와 결과 수집을 관리한다.

실행 시간을 비교할 때는 각 대표 작업을 `timing-start --task-id <task> --wave-id <wave> --wave-execution-id <unique-id> --fixture-id <fixture> --environment-id <environment>`와 반환된 ID를 사용한 `timing-finish --attempt-id <id> --log-file <local-log>`로 감싼다. 실행 ID는 wave·fixture·환경·계획·단조 시계 세션에 결합되며 동일 실행 안에서 같은 task를 재시작할 수 없다. 재시도에는 새 실행 ID를 쓴다. `timing-summary`는 시작·종료가 모두 있고 같은 실행·fixture·환경·시작 코드 지문·부팅 세션·계획 지문에 속하는 시도만 하나의 wave 시간으로 집계한다. 시작 코드 지문이 다르면 같은 실행 ID라도 별도 그룹으로 나눈다. 각 wave 시간은 병렬 시도들의 합이 아니라 같은 조건의 가장 이른 시작부터 가장 늦은 종료까지로 계산한다. 시작·종료 코드 지문과 로그 해시를 남긴다. 미완료 시도·계획 변경·세션 불일치는 성능 비교에서 제외하며, 이 기록은 품질 게이트나 PASS 판정에 영향을 주지 않는다. 기준선과 후보 실행이 모두 최종 게이트를 통과하고 같은 checkout·scope·review mode·review profile·필수 검사·QA 계획을 사용해야 한다. 두 실행의 task·wave·fixture·환경·계획 조합 집합이 정확히 같고, 모든 조합에서 실행별 안정된 시작 코드 지문을 가진 완료 측정이 3회 이상일 때 후보 실행에서 `timing-summary --baseline-run-dir <baseline-run-dir>`를 실행한다. 비교기는 각 조합의 중앙값을 계산하며 모든 조합에서 후보 중앙값이 더 낮을 때만 `improved`를 반환한다. 조합 누락·표본 부족·불안정한 코드 지문·조건 불일치·품질 게이트 실패는 `unverified`라서 개선을 주장하지 않는다.

## 역할과 결과

- 코디네이터: 요구사항과 실제 변경 영역을 판별하고 설계·검토·승인·검증의 증거를 로컬 작업 폴더에 기록한다. 공유 파일과 범위 변경을 관리한다.
- 영역별 설계자: 해당 영역의 설계 문서만 작성한다. 두 영역이 모두 필요하면 API·데이터 계약을 양쪽 문서에서 일치시킨다.
- 설계 검토자: light/balanced는 영역별 한 명이 전체 기준을 검토하고, full은 서로 다른 세 명이 슬롯 1 요구사항, 2 구조·호환성, 3 오류·보안·검증 가능성을 나눈다. 각자 위치·영향·수정안을 원본 문서에 보고한다.
- 계약 검토자: 두 영역이 관련될 때만 배정한다. balanced는 한 명, full은 서로 다른 세 명이 양쪽 설계의 요청·응답·오류·인증·호환성을 독립 대조한다.
- 영역별 구현자: 승인 게이트 뒤 지정된 파일 소유 범위만 수정한다. 공유 파일은 코디네이터가 순서를 정한다.
- 코드 검토자: light/balanced는 영역마다 한 명이 전체 항목을 검토하고, full은 서로 다른 세 명이 슬롯 1 설계 일치·회귀, 2 정확성·보안, 3 테스트·유지보수를 나눈다.
- QA 담당자: light는 한 명이 단일 영역의 모든 acceptance 기준을 normal·regression 대표 사례로 확인한다. balanced는 영역별·연동별 한 명씩 모든 계획 사례를 수행한다. full은 서로 다른 세 명이 슬롯 1 정상 흐름, 2 경계·오류·권한, 3 회귀·운영 환경을 나눠 실제 실행한다. 양쪽 작업의 계약·통합 QA도 같은 프로필별 인원 규칙을 따른다.

서브에이전트에게는 역할·작업 범위·읽을 문서·오케스트레이터가 출력한 슬롯별 `focus`와 질문·기대 산출물·편집 권한을 명시한다. `focus` 값은 결과 기록에도 그대로 사용한다. 검토 역할은 파일을 수정하지 않는다. 선택 프로필에 필요한 실제 canonical agent ID를 기록하고, full에서는 동일 단계·영역의 세 ID가 서로 다른지 확인한다. 필요한 인원을 확보하지 못하면 완료로 표시하지 않고 이유를 사용자에게 보고한다. 서브에이전트의 판단을 사용자 승인으로 취급하지 않는다. 코디네이터는 모든 결론·증거·상충 의견·미해결 항목과 원본 로컬 링크를 통합 리포트에 남긴다. 하네스는 agent ID 문자열의 실제 신원을 독립적으로 증명할 수 없다.

## 신규 policy-v4 실행 계획

신규 실행은 init에 `--policy-version 4`, `--task-size small|medium|large`, `--scope-goal`, `--in-scope`, `--out-of-scope`, `--completion-criterion`을 전달한다. 생성된 로컬 `scope-contract.json`은 요청 ID, 목표, 포함·제외 범위, 완료 기준, `owner_role: coordinator`를 보관한다. `orchestrate.py --run-dir <local-run-dir> --include-waves` 출력은 coordinator 한 명을 전체 owner로 표시하고 계약을 작업 그래프에 포함한다. 각 하위 작업은 할당된 경로를 contract와 대조하고, 사용자 결정에 종속될 수 있는 경로는 실행 전에 아래 guard로 확인한다.

```sh
python3 <plugin-root>/scripts/workflow_harness.py task-guard --run-dir <local-run-dir> --path <repository-relative-path>
```

종료 코드 3이면 현재 pending 필수 결정과 경로가 겹치거나 경로 의존성을 안전하게 해석할 수 없으므로 해당 작업만 보류한다. 독립 경로 작업은 계속한다. 미응답 선택 질문은 최종 보고와 완료를 차단하지 않는다.

설계 review digest는 영역마다 최대 두 개다. 첫 digest의 blocking finding을 사용자 승인한 뒤 딱 한 번 수정·재검토한다. 두 번째 결과에 finding이 남으면 `design-blocked` incomplete 보고서로 종료하고 세 번째 검토 digest는 새 실행에서 시작한다. low small 단일 영역의 QA plan은 `normal`·`regression`을 각각 하나 이상 포함하고 모든 acceptance criterion을 연결한다. 그 외 프로필은 적용 가능한 다섯 QA kind를 유지한다.

## v8 상태 스키마와 신규 계획

신규 v8 실행에서는 설계 검토 결과를 기록하기 전에 저장소의 의존성·실행 환경 버전에 맞춘 공식 문서/API 레퍼런스를 확인하고 `official-sources.json`을 작성한다. 각 출처는 ID, publisher, title, HTTPS URL, 문서 `version`, `checked_on`, 저장소에서 확인한 `detected_version`, `version_source`, `claims`, `applied_to`를 갖는다. 실행기는 형식을 검증하며, 코디네이터와 검토자가 detected version과 공식 문서 버전이 실제로 맞는지 대조한다. 적용 가능한 공식 문서는 적어도 하나여야 한다. 적용 자료가 없을 때만 `status: not_applicable`, 빈 `sources`, 구체적 `reason`을 기록한다. 기본 `pending` 상태는 설계 게이트를 막는다. 출처 매니페스트는 검사·설계·리뷰·QA·최종 보고 binding에 포함된다.

그다음 실행 폴더에 `verification-plan.json`과 `qa-plan.json`을 작성한다. 저장소에서 확인한 실제 명령만 계획에 넣는다. 전자는 영역마다 고유 검사 ID, 종류(`test`·`lint`·`build`·`other`), 명령, 필수 여부를 담는다. 각 작업 영역에는 필수 `test`가 적어도 하나 필요하며 선택 검사에는 이유를 쓴다. 예를 들어 백엔드만 작업한다면 다음 형태다.

```json
{"backend":[{"id":"unit-tests","kind":"test","command":"python3 -m unittest discover -s tests","required":true}]}
```

`qa-plan.json`은 정상·경계·권한·회귀·운영 다섯 `kind`를 모두 포함하고 영역·슬롯마다 고유 `scenario_id`, 해당 설계의 `## 수용 기준`에 선언한 `criterion_id`, 절차와 기대 결과를 담는다. 모든 적용 기준에 해당 영역의 QA 시나리오를 하나 이상 연결한다. 두 영역의 연동 QA 시나리오에는 기준이 속한 `source_area`도 지정한다.

```json
{"backend":[{"slot":1,"scenario_id":"R1-happy","criterion_id":"R1","kind":"normal","steps":"요청을 실행한다","expected":"정의된 응답을 받는다"},{"slot":2,"scenario_id":"R1-error","criterion_id":"R1","kind":"boundary","steps":"잘못된 요청을 실행한다","expected":"정의된 오류를 받는다"},{"slot":3,"scenario_id":"R1-regression","criterion_id":"R1","kind":"regression","steps":"관련 기존 흐름을 재실행한다","expected":"기존 동작이 유지된다"},{"slot":2,"scenario_id":"R1-auth","criterion_id":"R1","kind":"authorization","steps":"권한 없는 요청을 실행한다","expected":"거부된다"},{"slot":3,"scenario_id":"R1-ops","criterion_id":"R1","kind":"operations","steps":"재시작 뒤 요청을 실행한다","expected":"정상 동작한다"}]}
```

계획과 설계 문서는 실행 폴더 안에 둔다. `design` 게이트는 계획의 범위·중복·수용 기준 커버리지를 검사하고 설계 및 계획 파일의 해시를 기록한다. 설계나 계획이 바뀌면 이전 게이트는 무효다. 원문 요구사항 자체가 설계에서 빠졌는지는 하네스가 알 수 없으므로 설계 검토자와 코디네이터가 대조한다.

policy-v4의 질문 처리: `scope_extension`은 미응답이어도 핵심 구현·검증과 리포트 게시를 막지 않고 후속 항목으로 기록한다. `required_decision`은 `dependent_paths`와 작업 경로가 겹칠 때 해당 작업만 보류한다. 각 경로 실행 전에 `workflow_harness.py task-guard --run-dir <local-run-dir> --path <repo-relative-path>`를 호출한다. 종료 코드 3은 작업만 차단하며 무관한 경로는 계속한다. 답이 끝내 없으면 실제 완료 상태를 incomplete 보고서로 게시한다. v2-v3 실행에는 이 정책을 소급하지 않는다.

재개 시 policy-v4 계약과 pending 질문을 읽고 저장된 동일한 question ID·revision으로 UI를 복원한 뒤 `status`를 확인한다. 선택 질문은 답을 기다리지 않고 보고 단계까지 간다. 필수 결정이 남으면 답변 UI를 표시하되 종속되지 않은 작업은 수행한다. 이미 독립 작업이 끝난 경우 incomplete 보고서를 게시하고 `answer-question`을 반환한다. v2-v3 실행은 기존 대기·게시 규칙을 유지한다.

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
python3 <plugin-root>/scripts/workflow_harness.py scope-question --run-dir <local-run-dir> --question-id <stable-id> --kind <scope_extension|required_decision> --question <question> --path <affected-path> --impact <impact>
python3 <plugin-root>/scripts/workflow_harness.py scope-answer --run-dir <local-run-dir> --question-id <stable-id> --run-id <current-run-id> --revision <revision> --question-digest <digest> --status <approved|declined> --answer <answer> --reference <user-response>
python3 <plugin-root>/scripts/workflow_harness.py report-publish --run-dir <local-run-dir> --draft-file <local-report-draft.md>
```

`init`의 `--mode-reference`에는 이번 구현 요청에 대해 사용자가 두 방식 중 하나를 선택한 실제 응답을 기록한다. 하네스는 빈 참조를 거부하지만 그 응답의 진위를 독립적으로 확인하지는 못한다. `init`이 출력한 작업 폴더에 설계·검토·QA 리포트를 작성한다. `user-review`를 선택했다면 설계 초안 작성 직후 해당 문서의 클릭 가능한 절대 경로를 사용자에게 전달하고 `present`를 기록한다. 문서를 변경하면 다시 전달하고 `present`를 갱신한다. 설계 검토 원본은 선택 슬롯의 `{area}-design-review-{slot}.md`에 작성하고, 계약 원본도 선택 슬롯만큼 작성한다. 선택된 모든 결과를 수집한 뒤 한 명이라도 `changes-required`이면 발견 사항 전체를 보고하고 실제 사용자 승인 뒤에만 `approve`를 호출한다. 설계 변경 후 해당 프로필에 필요한 모든 검토자가 다시 검토한다. 영역별 통합 설계 검토 문서도 작성한다. `user-review`에서는 최종 설계와 검토 결과를 보여준 뒤 사용자 확인을 받았을 때만 `accept-design`을 기록한다.

`design` 게이트는 현재 설계 버전의 프로필별 개별 결과(light/balanced 1개, full 3개)·필요 시 서로 다른 agent ID·슬롯별 `focus`, 원본 문서와 계획의 변경 여부, 발견 사항의 승인 순서를 확인한다. `user-review`에서는 현재 설계를 보여준 기록과 동일한 버전의 사용자 확인도 요구한다. 이 게이트가 통과하기 전에는 신규 실행의 검증 결과를 기록할 수 없다. 코드 검토·QA 원본은 선택 프로필의 슬롯별 `{area}-code-review-{slot}.md`, `{area}-qa-{slot}.md`로 둔다. 필수 검사가 모두 PASS여야 코드 검토를 기록하고, 프로필에 필요한 모든 코드 검토가 CLEAR여야 QA 시나리오를 기록한다. 연동 QA는 연동 영역의 필수 검사와 양쪽 영역의 QA가 통과해야 기록한다. 각 QA 담당자의 최종 `agent-result pass`는 그 담당자에게 할당된 모든 시나리오가 PASS일 때만 기록한다. 코디네이터는 별도 통합 문서도 작성한다.

`final` 게이트는 현재 설계·계획·Git 코드 상태와 프로필별 코드 검토·QA 결과, 각 계획 시나리오의 최신 결과, 모든 적용 수용 기준의 커버리지, 최종 리포트를 확인한다. 모든 과거 자동 검증·QA 시도의 증거 파일은 실행 폴더 아래 비어 있지 않은 일반 파일이어야 하고, 시도마다 고유하며 원래 해시와 일치해야 한다. 심볼릭 링크와 실행 폴더 밖 경로는 사용할 수 없다. 두 영역이면 프로필에 필요한 연동 QA 원본과 통합 문서도 필요하다. 같은 코드 상태에서 코드 검토 `findings`를 해결해 다시 통과시키려면 `resolve-agent`에 해결 근거를 기록한 다음 결과를 다시 기록한다. 검사나 QA 시나리오 실패·미실행 뒤에는 각각 `resolve-check` 또는 `resolve-qa-case`로 원인·조치를 기록하고 같은 ID를 새 증거로 재실행한다. 환경 복구나 설명만으로 PASS로 바꾸지 않는다. 코드나 설계가 바뀌면 관련 검사·프로필별 검토·QA를 새 상태에서 다시 수행한다. 필수 미실행 및 QA `not-run`은 완료가 아니다. 기록된 agent ID, 사용자 응답, 증거 내용의 진실성은 하네스가 독립적으로 증명할 수 없으므로 코디네이터가 실제 결과를 확인한다.

사용자 질문에는 해당 질문 유형에 허용된 Codex 질문 UI를 우선 사용해 선택지와 자유 입력을 제공한다. 도구 결과에서 질문 등록을 확인한 경우에만 UI 전달 성공으로 본다. UI가 없거나 실패한 경우 같은 질문과 선택지를 일반 대화로 제시한다. 정확한 버튼 이름은 Codex 클라이언트가 제어한다. 검토 지연 시간은 운영상의 soft target으로 관리한다. 제한 시간을 넘어도 판단이 오지 않으면 지연 슬롯과 blocker를 기록하고 독립 작업을 계속하되 해당 검토를 통과 처리하지 않는다. 검토 지연 정책은 사용자 질문의 응답 대기를 끝내는 근거가 되지 않는다. 기존 v2~v7 완료 실행은 과거 기록으로 유지하며, 미완료 실행은 정확히 재개할 때 현재 v8 게이트로 승격하고 이전 미결합 PASS를 다시 검증한다.

최종 보고서는 실행 폴더에 작성하고 `report-publish`로 현재 코드·설계·계획·질문·출처에 묶어 게시한다. 변경 파일과 코드 위치, 동작 원리, 검증 결과, 요청 밖 문제와 질문 상태를 포함한다. 제품·도메인 정책을 설명하는 경우에만 코드 주석을 추가한다.
