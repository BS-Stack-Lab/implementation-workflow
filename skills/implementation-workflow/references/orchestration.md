# 서브에이전트 운영과 하네스

## 범위별 작업 그래프

플러그인 루트는 이 `SKILL.md` 파일의 두 단계 위 디렉터리다. 범위를 판별한 다음 다음 명령으로 작업 그래프를 읽는다.

```sh
python3 <plugin-root>/scripts/orchestrate.py --scope frontend
python3 <plugin-root>/scripts/orchestrate.py --scope backend
python3 <plugin-root>/scripts/orchestrate.py --scope both
```

실제 범위에 해당하는 명령 한 가지만 실행한다. 그래프의 `depends_on`을 지킨다. `both`일 때 각 영역의 설계와 검토는 독립적으로 진행할 수 있지만, 계약 검토와 승인 게이트가 끝나기 전에는 구현하지 않는다. 스크립트는 계획을 출력하며 에이전트를 직접 생성하지 않는다. 코디네이터가 사용 가능한 Codex 서브에이전트 도구로 역할을 배정하고 결과를 수집한다.

## 역할과 결과

- 코디네이터: 요구사항과 실제 변경 영역을 판별하고 설계·검토·승인·검증의 증거를 로컬 작업 폴더에 기록한다. 공유 파일과 범위 변경을 관리한다.
- 영역별 설계자: 해당 영역의 설계 문서만 작성한다. 두 영역이 모두 필요하면 API·데이터 계약을 양쪽 문서에서 일치시킨다.
- 영역별 설계 검토자: 설계 작성에 참여하지 않은 읽기 전용 서브에이전트다. 요구사항·설계·관련 코드를 비교해 위치·영향·수정안을 보고한다.
- 계약 검토자: 두 영역이 관련될 때만 양쪽 설계의 요청·응답·오류·인증·호환성을 대조한다.
- 영역별 구현자: 승인 게이트 뒤 지정된 파일 소유 범위만 수정한다. 공유 파일은 코디네이터가 순서를 정한다.
- 영역별 코드 검토자: 해당 구현에 참여하지 않은 읽기 전용 서브에이전트다. diff, 설계, 수용 기준과 테스트를 비교한다.
- 영역별 QA 담당자: 실제 실행 결과와 증거를 해당 QA 리포트에 기록한다. 양쪽이 관련될 때만 연동 QA를 추가한다.

서브에이전트에게는 역할·작업 범위·읽을 문서·기대 산출물·편집 권한을 명시한다. 검토 역할은 파일을 수정하지 않는다. 서브에이전트가 없으면 독립 검토가 불가능했다는 점을 보고하고 별도 수동 검토를 기록한다. 서브에이전트의 판단을 사용자 승인으로 취급하지 않는다.

## 로컬 하네스 게이트

```sh
python3 <plugin-root>/scripts/workflow_harness.py init --repo <target-repo> --scope <frontend|backend|both>
python3 <plugin-root>/scripts/workflow_harness.py review --run-dir <local-run-dir> --area <area> --result <clear|changes-required> [--finding <summary>]
python3 <plugin-root>/scripts/workflow_harness.py approve --run-dir <local-run-dir> --area <area> --reference <actual-user-approval>
python3 <plugin-root>/scripts/workflow_harness.py check --run-dir <local-run-dir> --gate design
python3 <plugin-root>/scripts/workflow_harness.py check-record --run-dir <local-run-dir> --area <area> --name <check-name> --status <pass|fail|not-run> --evidence <actual-result>
python3 <plugin-root>/scripts/workflow_harness.py check --run-dir <local-run-dir> --gate final
```

`init`이 출력한 작업 폴더에 설계·검토·QA 리포트를 작성한다. `review` 전에 해당 설계 검토 기록을 작성한다. 검토 결과가 `changes-required`이면 실제 사용자 승인 이후에만 `approve`를 호출한다. 승인받은 설계를 수정하고 다시 검토해 `clear`를 기록한다. 두 영역을 작업하면 `integration-contract-review.md`와 `integration` 영역 검토도 필요하다.

`design` 게이트는 필요한 설계·검토 문서, 승인 순서, 현재 설계의 재검토 여부를 확인한다. `final` 게이트는 영역별 코드 검토·QA 문서와 검증 기록, 두 영역 작업 시 연동 QA, 최종 리포트를 확인한다. 실패한 검증은 같은 이름으로 재검증 결과를 기록해야 통과한다. 실행할 수 없는 검증은 `not-run`과 사유를 남긴다. 기록된 사실을 하네스가 독립적으로 증명할 수는 없으므로 실제 명령 출력과 관찰 결과를 보고서에 넣는다.
