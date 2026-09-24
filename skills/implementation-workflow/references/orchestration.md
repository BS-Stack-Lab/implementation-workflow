# 서브에이전트 운영과 하네스

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
- 영역별 설계 검토자 3명: 설계 작성에 참여하지 않은 읽기 전용 서브에이전트다. 요구사항·설계·관련 코드를 독립적으로 비교해 위치·영향·수정안을 각자의 문서에 보고한다.
- 계약 검토자 3명: 두 영역이 관련될 때만 양쪽 설계의 요청·응답·오류·인증·호환성을 독립적으로 대조한다.
- 영역별 구현자: 승인 게이트 뒤 지정된 파일 소유 범위만 수정한다. 공유 파일은 코디네이터가 순서를 정한다.
- 영역별 코드 검토자 3명: 해당 구현에 참여하지 않은 읽기 전용 서브에이전트다. diff, 설계, 수용 기준과 테스트를 독립적으로 비교한다.
- 영역별 QA 담당자 3명: 해당 구현에 참여하지 않은 서로 다른 서브에이전트다. 실제 실행 결과와 증거를 각자의 QA 문서에 기록한다. 양쪽이 관련될 때 연동 QA도 3명이 수행한다.

서브에이전트에게는 역할·작업 범위·읽을 문서·기대 산출물·편집 권한을 명시한다. 검토 역할은 파일을 수정하지 않는다. 각 단계의 실제 canonical agent ID를 기록하고 동일 단계·영역의 세 ID가 다른지 확인한다. 세 명을 확보하지 못하면 완료로 표시하지 않고 이유를 사용자에게 보고한다. 서브에이전트의 판단을 사용자 승인으로 취급하지 않는다. 코디네이터는 세 결론·증거·상충 의견·미해결 항목과 원본 로컬 링크를 통합 리포트에 남긴다. 하네스는 agent ID 문자열의 실제 신원을 독립적으로 증명할 수 없다.

## 로컬 하네스 게이트

```sh
python3 <plugin-root>/scripts/workflow_harness.py init --repo <target-repo> --scope <frontend|backend|both> --review-mode <immediate|user-review> --mode-reference <user-answer-for-this-request>
python3 <plugin-root>/scripts/workflow_harness.py present --run-dir <local-run-dir> --area <frontend|backend>
python3 <plugin-root>/scripts/workflow_harness.py review --run-dir <local-run-dir> --area <area> --slot <1|2|3> --agent-id <actual-subagent-id> --result <clear|changes-required> [--finding <summary>]
python3 <plugin-root>/scripts/workflow_harness.py approve --run-dir <local-run-dir> --area <area> --reference <actual-user-approval>
python3 <plugin-root>/scripts/workflow_harness.py accept-design --run-dir <local-run-dir> --reference <actual-user-confirmation>
python3 <plugin-root>/scripts/workflow_harness.py check --run-dir <local-run-dir> --gate design
python3 <plugin-root>/scripts/workflow_harness.py check-record --run-dir <local-run-dir> --area <area> --name <check-name> --status <pass|fail|not-run> --evidence <actual-result>
python3 <plugin-root>/scripts/workflow_harness.py agent-result --run-dir <local-run-dir> --stage code-review --area <frontend|backend> --slot <1|2|3> --agent-id <actual-subagent-id> --result <clear|findings>
python3 <plugin-root>/scripts/workflow_harness.py agent-result --run-dir <local-run-dir> --stage qa --area <area> --slot <1|2|3> --agent-id <actual-subagent-id> --result <pass|fail|not-run>
python3 <plugin-root>/scripts/workflow_harness.py resolve-agent --run-dir <local-run-dir> --stage <code-review|qa> --area <area> --slot <1|2|3> --reference <resolution-evidence>
python3 <plugin-root>/scripts/workflow_harness.py check --run-dir <local-run-dir> --gate final
```

`init`의 `--mode-reference`에는 이번 구현 요청에 대해 사용자가 두 방식 중 하나를 선택한 실제 응답을 기록한다. 하네스는 빈 참조를 거부하지만 그 응답의 진위를 독립적으로 확인하지는 못한다. `init`이 출력한 작업 폴더에 설계·검토·QA 리포트를 작성한다. `user-review`를 선택했다면 설계 초안 작성 직후 해당 문서의 클릭 가능한 절대 경로를 사용자에게 전달하고 `present`를 기록한다. 문서를 변경하면 다시 전달하고 `present`를 갱신한다. 설계 검토 원본은 `{area}-design-review-1.md`부터 `-3.md`까지 작성하고, 두 영역 작업의 계약 원본은 `integration-contract-review-1.md`부터 `-3.md`까지 작성한다. 세 결과를 모두 수집한 후 한 명이라도 `changes-required`이면 발견 사항 전체를 보고하고 실제 사용자 승인 뒤에만 `approve`를 호출한다. 설계 변경 후 세 명 모두 다시 검토한다. 영역별 통합 설계 검토 문서도 작성한다. `user-review`에서는 최종 설계와 검토 결과를 보여준 뒤 사용자 확인을 받았을 때만 `accept-design`을 기록한다.

`design` 게이트는 현재 설계 버전에 대한 세 개별 결과와 서로 다른 agent ID, 원본 문서의 변경 여부, 발견 사항의 승인 순서를 확인한다. `user-review`에서는 현재 설계를 사용자에게 보여준 기록과 동일한 버전의 사용자 확인도 요구한다. 코드 검토 원본은 `{area}-code-review-1.md`부터 `-3.md`, QA 원본은 `{area}-qa-1.md`부터 `-3.md`다. 코디네이터는 별도 통합 문서도 작성한다. `final` 게이트는 세 코드 검토가 모두 `clear`, 세 QA가 모두 `pass`인지와 현재 설계·Git 코드 상태·원본 문서의 해시, 검증 기록, 최종 리포트를 확인한다. 두 영역이면 연동 QA 원본 세 건과 통합 문서도 필요하다. 같은 코드 상태에서 코드 검토 `findings`나 QA `fail`·`not-run`을 해결해 다시 통과시키려면 `resolve-agent`에 해결 근거를 기록한 다음 해당 서브에이전트 결과를 다시 기록한다. 코드나 설계가 바뀌면 해당 세 검토·QA와 검증을 새 상태에서 다시 수행한다. 실패한 검증은 같은 이름으로 재검증 결과를 기록해야 통과한다. QA `not-run`은 완료가 아니다. 기록된 agent ID나 `--reference` 답변의 진위를 하네스가 독립적으로 증명할 수 없으므로 실제 ID와 받은 응답만 기록한다.

사용자 질문에는 해당 질문 유형에 허용된 Codex 질문 UI를 우선 사용해 선택지와 자유 입력을 제공한다. UI가 없으면 일반 대화로 질문한다. 정확한 버튼 이름은 Codex 클라이언트가 제어한다. 기존 v2 작업 폴더는 이전 CLI와 게이트로 계속 확인하며 신규 v3 작업에는 위 명령을 사용한다.
