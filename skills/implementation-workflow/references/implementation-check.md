# 구현·검사 단계

1. `workflow_harness.py status --run-dir <run-dir>`로 현재 단계와 차단 사유를 확인한다. `design` 게이트가 현재 설계·계획에 대해 통과했으면 승인 설계의 범위대로 구현한다. `immediate` 작업은 게이트 통과 후 같은 작업에서 계속한다. 범위가 바뀌면 설계로 돌아간다.
2. v5의 두 영역 작업에 `impact-map.json`이 있으면 frontend/backend/shared glob마다 소유 근거를 적는다. `shared/`, `contracts/`, `schema/`, `schemas/`의 루트·하위 파일과 루트 설정·lockfile·배포/보안 파일은 지도와 독립적으로 공유다. 소유가 불명확하면 두 영역 모두 다시 검사한다. 지도가 없으면 전체 코드 해시를 사용한다. 지도 오류는 설계 게이트 실패다. 수용 기준별 경로는 검토 문맥에만 쓰고 같은 영역 QA를 건너뛰지 않는다.
3. `verification-plan.json`의 필수 명령을 각 영역의 지정된 저장소에서 실제로 실행한다. `python3 <plugin-root>/scripts/run_check.py --run-dir <run-dir> --repo <area-repo> --area <area> --command '<planned-command>'`는 해당 체크아웃·영역·코드 해시·로그·종료 코드를 manifest에 남긴다. 연동 검사는 지정된 연동 저장소에서 실행하고 FE·BE·연동 저장소의 코드 상태를 함께 확인한다. 연결 작업 트리의 git-dir가 바뀌면 이전 증거를 재사용하지 않는다. 단일 영역에서는 `--area`를 생략할 수 있다. `evidence_summary.py --run-dir <run-dir> --manifest-file <path>`는 짧은 전달용 요약일 뿐 판정을 바꾸지 못한다.
4. `check-record --area <area> --name <planned-id> --status <pass|fail> --manifest-file <path>`는 manifest의 계획 명령·종료 코드·로그 해시와 일치해야 한다. 실패·미실행 뒤에는 `resolve-check`에 원인·조치를 남기고 **새 실행 시도**로 재검사한다. 환경 복구나 설명만으로 PASS로 기록하지 않는다. 선택 검사의 미실행은 `--status not-run --reason --unverified`로 기록한다.
5. 변경한 영역의 필수 검사가 모두 PASS여야 코드 검토를 시작한다. 검토 후 코드가 바뀌면 현재 코드 기준으로 관련 검사·선택한 1인/3인 코드 검토·QA를 다시 수행한다. light에서 위험 경로가 추가되면 full 실행으로 다시 시작한다. 테스트는 실제 변경 위험을 확인하는 수준으로 둔다. 단계가 중단되면 같은 실행 폴더의 `status`를 조회해 `next_action`부터 계속한다.

모든 로그·manifest·요약은 로컬 실행 폴더에 보관한다. `README.md` 외 생성 문서를 Git에 커밋·푸시·병합하지 않는다.
