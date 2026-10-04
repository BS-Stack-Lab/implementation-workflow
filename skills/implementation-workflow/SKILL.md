---
name: implementation-workflow
description: 사용자가 문서나 요구사항을 바탕으로 코드 구현·기능 추가·버그 수정을 요청하거나 진행 중인 구현을 이어갈 때 설계, 독립 검토, 승인, 구현, 테스트, 코드 리뷰, QA와 최종 보고를 단계적으로 수행한다. 설명·계획만 요청한 경우에는 적용하지 않는다.
---

# 문서 기반 구현 워크플로

요청된 구현을 끝까지 수행한다. 저장소 지침·관련 스킬·실제 검사 명령을 확인한다. 시작할 때 요청 범위를 `scope-contract.json`으로 고정하고 작업 ID와 기존 로컬 실행을 확인한다. policy-v4 재개 시 계약과 `scope-register.json`의 pending 질문을 확인한 뒤 `workflow_harness.py status --run-dir <run-dir>`에서 다음 단계를 이어간다. v2-v3 실행은 기존 `status` 규칙을 유지한다. 새 작업에만 `init`을 사용한다. 전체 실행의 owner는 coordinator 한 명이며, 서브에이전트는 배정된 경로와 검토 질문만 처리한다.

| 단계 | 필수 참고 파일 | 다음 단계 조건 |
| --- | --- | --- |
| 요청·설계·독립 검토 | [intake-design.md](references/intake-design.md) | 선택한 1인/3인 독립 검토, 필요한 사용자 승인, `design` 게이트 통과 |
| 구현·자동 검사 | [implementation-check.md](references/implementation-check.md) | 현재 코드의 필수 검사 PASS |
| 코드 검토·QA·보고 | [review-qa.md](references/review-qa.md) | 선택한 1인/3인 코드 검토·QA·적용 기준 PASS, `final` 게이트 통과 |

## 모든 단계의 불변 규칙

- **매 새로운 구현 요청마다** Codex 질문 UI가 있으면 `request_user_input_async`를 직접 호출해 사용자에게 묻는다. canonical 질문은 “설계 문서를 작성한 뒤 어떻게 진행할까요?”이며 선택지는 “독립 설계 검토 후 바로 구현 (추천)”과 “설계 문서를 직접 검토한 뒤 구현”이다. 도구 입력에 자유 입력도 제공한다. `accepted: true`는 질문 전달 접수일 뿐 답변이 아니다. 명시적 사용자 답변 전에는 이전 선택을 재사용하거나 추정하지 않는다. 질문만 보낸 뒤 turn을 끝내지 않는다. 답이 필요한 작업이 남아 있으면 독립 작업을 계속하고, 답변 외에 막힌 일이 없으면 `clock.sleep`을 최대 60초 간격으로 반복해 이 turn을 유지한다. 시간 경과나 침묵으로 질문을 완료 처리하거나 incomplete 보고서를 게시하지 않는다. 실제 답변 또는 명시적 거절만 대기를 끝낸다. 앱/turn 중단 뒤에는 동일 작업을 재개해 같은 질문을 다시 띄운다. run이 시작된 질문은 저장된 ID·revision·본문·선택지를 재사용하고, 시작 전 검토 방식 질문은 동일한 canonical 질문·선택지를 다시 사용한다. 질문 UI가 없거나 호출에 실패하면 같은 내용을 대화 질문으로 제시하고 같은 대기 규칙을 적용한다. 버튼 모양은 클라이언트가 결정한다.
- 설계 문서 작성만 요청한 작업은 설계 전달에서 끝낸다. 구현까지 요청하고 `immediate`를 선택했다면 현재 설계의 `design` 게이트가 통과하는 즉시 같은 작업에서 구현·검사·코드 검토·QA·최종 보고를 계속한다. `user-review`는 명시적 설계 확인만 기다린다. 중단 후 policy-v4 실행은 scope contract·pending 질문·`status`를 읽어 빠진 단계부터 재개한다. v2-v3은 기존 재개 순서를 따른다. 선택 질문은 완료를 막지 않고, 필수 결정 질문만 해당 종속 경로를 멈춘다. 필수 응답이 미도착한 상태에서 turn이 살아 있으면 대기를 유지하고 incomplete 보고서를 게시하지 않는다. 실제 turn이 종료되어 작업이 중단된 경우에만 run을 미완료 상태로 보존해 재개한다.
- 신규 실행은 `--policy-version 4`와 필수 scope contract 인수를 사용한다. 계약에는 목표, 포함·제외 범위, 완료 조건을 간결하게 기록한다. 작업이 계약 범위를 바꾸면 현재 실행을 수정하지 않고 사용자 응답을 기록한 뒤 새 작업 실행을 만든다. 재개 때도 같은 계약 해시를 확인한다.
- 실제 작업 영역을 판별해 프론트엔드만이면 `frontend-design.md`·`frontend-qa.md`, 백엔드만이면 `backend-design.md`·`backend-qa.md`, 둘 다면 각각과 연동 결과를 작성한다. 설계의 수용 기준 ID와 QA 시나리오를 연결한다.
- 작업 크기를 small/medium/large로 분류한다. small은 한 영역·한 동작·최대 3개 파일이며 계약·저장 데이터·보안·권한 영향이 없다. medium은 한 영역에서 파일이 4개 이상이거나 여러 수용 기준·분기를 바꾼다. large는 대형 양 영역 연동·공유 계약·스키마·운영 파일 또는 여러 독립 기능을 포함한다. 변경 경로가 분리되고 계약을 바꾸지 않는 제한된 양 영역 작업은 medium으로 분류할 수 있다. small+low 단일 영역은 `light`, medium 단일 영역 또는 경로가 분리된 low/medium 양 영역은 `balanced`, large·high·unknown 및 고영향 변경은 `full`이다. 단계는 모두 유지하며 light는 검토자 1명과 normal/regression 최소 QA로 줄인다. balanced는 각 영역 1명, full은 서로 다른 3명으로 한다. 위험 상향은 새 full 실행에서 재검증한다. 과거 실행 규칙은 보존한다.
- 설계 검토는 초기 wave 1회와 수정 승인 후 재검토 1회로 제한한다. 재검토 뒤 blocking finding이 남으면 구현하지 않고 design-blocked incomplete 보고서를 게시한다. 세 번째 설계 digest로 검토를 시작하지 않고 새 실행을 만든다.
- 새 테스트 코드는 위험과 관찰 가능한 동작 변화에 따라 판단한다. 작고 제한된 변경은 계약·상태·오류·권한·데이터·설정·연동 변화, 사용자 관찰 결과의 차이, 보안·결제·개인정보·데이터 손실·동시성 위험이 없고 기존/대체 검증으로 충분한 경우 `low-risk-simple-change`로 새 테스트 코드를 생략할 수 있다. 위험이나 영향이 불명확하면 새 테스트 코드를 작성·수정한다. 생략은 지정 코드 검토자가 근거를 확인하고 기록한다. 테스트 코드 작성 여부와 무관하게 영역별 필수 테스트 명령을 실행한다.
- 구현 전에 저장소의 버전·의존성을 직접 확인하고, 일치하는 공식 공급자 문서·표준·API 레퍼런스를 찾는다. 감지한 기술 버전과 확인 근거, 출처의 버전·날짜·기술 주장·적용 코드 경로를 `official-sources.json`에 기록한다. 실행기는 형식을 확인하고 실제 버전 일치 여부는 코디네이터와 검토자가 공식 자료 및 저장소 설정을 대조한다. 공식 근거가 없거나 적용되지 않으면 이유와 대체 검증을 기록한다. 출처가 적용되는데 확인하지 않았다면 구현을 시작하지 않는다.
- 질문 UI 항목을 만들면 같은 질문 ID·revision·본문·선택지로 재개 시 복원한다. `scope_extension`은 선택 질문이므로 미응답이어도 핵심 구현·검증·리포트 게시를 막지 않고 후속 항목으로 기록한다. `required_decision`은 `dependent_paths`와 작업 경로가 겹칠 때만 해당 작업을 막는다. 독립 단계는 계속하며, 막힌 작업만 남고 현재 turn이 살아 있으면 `clock.sleep`을 최대 60초씩 반복해 답변 UI 대기를 유지한다. 침묵이나 시간 경과로 pending을 unanswered로 바꾸거나 incomplete 보고서를 게시하지 않는다. 실제 turn 중단 시 run을 보존하고 재개 시 UI를 다시 띄운다. 모호한 답은 승인으로 간주하지 않는다.
- v2-v3 실행의 저장 상태 형식은 유지한다. 질문을 현재 turn에서 전달한 뒤에는 버전과 관계없이 명시 응답 전까지 대화를 끝내지 않는다. policy-v4의 필수 결정은 실제 turn이 중단된 경우에만 pending 상태의 미완료 실행으로 보존하며, 다음 turn에서 같은 질문을 다시 표시한다. 선택 질문은 핵심 작업·리포트 게시를 막지 않는다. 질문 상태와 scope contract digest는 보고서 evidence binding에 포함한다.
- incomplete 리포트 뒤에는 미응답 또는 거절된 `required_decision`을 같은 run에서 새 revision/digest로 다시 답할 수 있다. 과거 답변 이벤트와 리포트는 이력으로 보존하며, 새 답변은 설계·검증 evidence를 자동 승인하지 않는다.
- 질문 revision은 질문별로 증가한다. `scope_extension`은 첫 답변 후 terminal이며, `required_decision`만 현재 incomplete 리포트에 연결된 새 revision/digest로 다시 답할 수 있다. 승인 범위가 바뀌면 설계 검토부터 새 증거를 만든다.
- 미완료 구형 실행을 현재 게이트로 승격할 때 시작 기준 커밋이 없거나 저장 커밋을 찾을 수 없으면 추정하지 않는다. 사용자가 확인한 `--legacy-base AREA=REF`를 요구하고 현재 checkout의 조상인지 확인한다.
- 최종 리포트의 코드 줄 표기는 `area:path:new:N`을 사용하고 실제 post-change diff 줄과 맞춘다. 순수 삭제는 `area:path:old:N (baseline:<40자리 SHA>)`로 인용해 기준 객체를 식별한다.
- v2-v7 과거 완료는 각 버전의 기존 `check_final` 규칙으로 판정한다. 새 v8 필수 evidence 규칙을 과거 완료 기록에 소급하지 않는다.
- `orchestrate.py --include-waves`가 출력한 결정적 wave를 사용한다. 슬롯이 제한되면 현재 wave의 대기 작업을 큐에 두고 빈 슬롯마다 시작하며, wave 전체 완료 전 다음 단계로 이동하지 않는다. 같은 checkout의 FE/BE 구현과 검토는 항상 직렬화한다. 서로 다른 checkout은 exact 경로 소유권이 증명되고 변경 후 tracked·untracked·deleted·rename 경로가 계획과 일치할 때만 병렬화한다. 설계 검토는 짧은 브리프와 역할 분리로 줄인다. timeout이나 검토 미완료를 통과로 간주하지 않는다.
- 오케스트레이터는 계획을 출력하는 데 그치지 않고 해당 Codex 세션의 native `collaboration.spawn_agent` 도구로 계획의 `executor: subagent`, `agent_key`, `agent_action: spawn` 작업을 실제 배정한다. `reuse` 작업은 같은 `agent_key`의 구현 에이전트에게 보낸다. 계획의 coordinator 작업은 주 에이전트가 계속 소유한다. 구현 에이전트는 구현 직후 해당 영역 테스트를 이어서 수행할 수 있지만, 설계 검토·코드 검토·QA는 결과 작성자와 다른 에이전트가 수행해야 한다. 각 spawn에 지정 역할·정확한 변경 경로·쓰기 가능 여부·공식 출처·완료 기준을 전달하고 실제 반환된 agent ID와 task 결과 경로를 로컬 dispatch 기록에 저장한다. 기존 harness의 리뷰·QA 결과는 stage/area/slot과 실제 agent ID를 유지한다. 하네스에 없는 일반 구현 task-to-agent 증거를 있다고 주장하지 않는다.
- 호스트가 동시 슬롯 수를 명시하면 그 한도 안에서 실행한다. 제공하지 않으면 한 번에 한 서브에이전트만 실행한다. 용량 초과 응답은 wave 대기열에 남겨 두고 실행 중 에이전트가 완료된 후 다시 시도한다. 도구가 없거나 활성 실행 없이 생성에 실패하면 가짜 ID를 기록하지 말고 해당 필수 단계·실행을 `incomplete`로 남긴다. wave의 모든 결과를 회수한 뒤에만 다음 wave로 진행한다. Python 스크립트는 계획을 만들 뿐 native agent를 직접 생성하지 않으며, 별도 API 키나 API 세션을 만들지 않는다.
- 현재 단계 그래프에는 통합 코드 리뷰가 없으므로 `impact-map.json`이 shared glob 또는 exact shared 경로를 선언하면 양 영역 구현을 시작하지 않는다. shared 계약만 소유 저장소의 단일 영역 `full` run에서 변경·검토·QA까지 완료하고, 검토된 소스 계약을 로컬 커밋해 다음 run의 `HEAD` 기준선에 포함한다. 그 다음 FE/BE run을 시작해 shared 경로를 변경하지 않는다. 이 커밋은 로컬 기준선 고정 용도이며 push하지 않는다. 같은 checkout에서도 backend 구현은 frontend 코드 검토가 끝난 뒤 시작하며, 실제 변경 경로 검증을 생략하지 않는다. 작업 중 예상 밖 공유 경로가 발견되면 QA로 넘기지 말고 실행을 중단해 변경 범위를 다시 설계한다.
- 설계는 작성에 참여하지 않은 읽기 전용 서브에이전트가 선택한 인원만큼 독립 검토한다. 한 명이라도 수정을 요구하면 모든 결과를 보고하고 **사용자 설계 변경 승인 후** 한 번에 반영한다. 같은 인원이 한 차례 재검토한다. 두 번째 검토에도 차단 finding이 남으면 구현하지 않고 incomplete 리포트로 끝낸다. `user-review`에서는 로컬 설계 링크와 최종 확인을 제공한다. `design` 게이트 전에는 구현하지 않는다.
- 구현 후 필수 검사를 실제 통과시키고 구현에 참여하지 않은 별도 에이전트에게 코드 검토와 QA를 맡긴다. light QA는 모든 acceptance 기준을 연결한 normal·regression 대표 사례를 실행하며 위험과 실패 비용에 따라 보강한다. 실패를 설명만으로 PASS로 바꾸지 않는다. 코드 변경 후 해당 검사·검토·QA를 현재 상태에서 다시 수행한다.
- 단계별 역할·초점·추천 모델/effort는 `orchestrate.py`의 그래프를 따른다. 기본 검토자는 Luna이며 자동 Sol 재검토자는 만들지 않는다. 실제 모델·대체 사유를 기록한다. 서브에이전트는 `review_brief.py`의 짧은 브리프와 필요한 원문만 받고 `fork_turns: none`을 우선 사용한다. 의존 파일은 필요 시 확장한다.
- Codex 사용량 도구가 있으면 주간 usedPercent를 시작·단계 전후에 확인하고 `scripts/usage_checkpoint.py --run-dir <local-run-dir> --label <stage> --used-percent <integer>`로 기록한다. 도구가 없으면 `--unavailable`로 기록한다. `stop-next-optional-call`이면 추가 선택 호출을 중단하고 상태를 보고한다. 정수 퍼센트·공유 계정 창·진행 중 호출 때문에 작업당 1% 미만은 보장할 수 없다.
- 신규 실행은 `run_check.py`가 영역별 저장소에서 검사하고 원본 로그·manifest를 보존한다. `render_result.py`는 검토·QA JSON에서 Markdown을 만든다. 하네스는 원본 해시·판정·계획·각 코드 상태를 검증한다. 이전 실행은 해당 버전의 인원 규칙으로 계속 읽는다.
- 생성하는 설계·검토·QA·최종 Markdown과 로그는 **관련 Git 저장소 모두의 밖인 `~/Documents/docs/`의 해당 작업 폴더**에 보관한다. 같은 작업의 미완료 실행은 재사용한다. 새 실행은 `--new-run`이 명시된 경우에만 만든다. `README.md` 외 문서는 `SKILL.md`를 포함해 기본적으로 커밋·푸시·병합하지 않는다. 사용자가 특정 문서 게시를 직접 요청한 경우에만 해당 예외를 검토한다. 로컬 훅만으로 GitHub 전체 차단을 보장하지 않으므로 원격 규칙도 확인한다.
- 로컬 문서 차단을 쓰려면 system/global/local/worktree 어디에도 설정된 `core.hooksPath`가 없는 저장소에만 플러그인의 `git-hooks` 경로를 local로 연결한다. 어떤 범위에든 기존 값이 있으면 덮어쓰기·복사·자동 chaining을 하지 않는다. 현재 유효 경로가 플러그인 경로와 다르면 guard가 자동 실행되지 않을 수 있음을 로컬 최종 리포트에 기록하고, 연결은 사용자 또는 저장소 관리자가 결정한다. private/internal 저장소의 서버 push ruleset은 GitHub Team 조건과 활성화된 ruleset fork 범위를 공식 문서에서 확인한다.
- 최종 리포트는 바뀐 파일과 코드 위치, 동작 원리, 실행한 검증, 요청 밖 질문과 답변 상태를 포함한다. 하네스의 `report-publish`가 초안을 검증한 뒤 로컬에서 원자적으로 게시하고, `check --gate final`과 `status`가 현재 코드·설계·계획·질문·리포트의 결합을 확인해야 완료다.
- 선택한 검토 인원, 사용자 승인, 필수 검사, QA 증거 중 하나라도 없으면 완료로 표시하지 않는다. `final-report.md`는 실제 사용량 관측 범위와 미검증 범위, 발견·수정한 문제, 로컬 원본 링크를 정확히 담는다.
- 제품·도메인 정책 외 코드 주석은 추가하지 않는다. 코드 변경 자체로 의도가 명확하지 않은 정책은 주석으로 설명할 수 있다.
