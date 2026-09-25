# Implementation Workflow

문서 기반 코드 구현을 위한 Codex 스킬, `UserPromptSubmit` 자동 라우팅 훅, 로컬 단계 검증 하네스와 서브에이전트 작업 그래프입니다. 새 구현 요청마다 설계 검토 후 바로 구현할지 사용자가 직접 설계를 검토할지 질문 UI로 묻습니다. 동일 작업을 이어갈 때는 기존 실행의 선택과 상태를 사용합니다. 단일 영역의 작고 국소적인 작업은 설계·코드 검토와 QA에 각 1명, 위험하거나 영향이 불명확한 작업과 두 영역 작업은 각 3명을 배정합니다. 필수 검사·사용자 설계 변경 승인·QA 증거 게이트는 두 모드 모두 유지합니다. 작업당 Pro 주간 사용량 1% 미만은 계정의 정수 퍼센트 지표와 공유 창 때문에 보장할 수 없으며, 측정값과 한계를 보고합니다.

## 중단 후 이어하기와 다중 저장소

`init --work-item <작업-ID>`는 같은 작업의 미완료 로컬 실행을 찾아 재사용합니다. 새 실행이 필요하면 `--new-run`을 명시합니다. `workflow_harness.py status --run-dir <실행-폴더>`는 `stage`, `next_action`, `blocker`, `complete`를 JSON으로 보여줍니다. `orchestrate.py --run-dir <실행-폴더>`는 그 상태와 해당 실행의 작업 그래프를 함께 출력합니다. 설계 게이트가 통과한 `immediate` 구현 요청은 같은 작업에서 구현·테스트·코드 검토·QA·최종 보고를 계속합니다. `user-review`는 사용자의 설계 확인을 기다리고, 설계 문서만 요청한 작업은 설계 전달에서 끝납니다.

프론트엔드와 백엔드가 다른 Git 저장소면 `init --scope both --frontend-repo <FE-저장소> --backend-repo <BE-저장소>`로 각각 지정합니다. 같은 저장소를 두 인수에 줄 수도 있습니다. `--integration-repo`를 지정하지 않으면 백엔드 저장소에서 연동 검사를 실행합니다. 별도 통합 저장소를 지정하면 연동 게이트는 FE·BE·통합 저장소 세 곳의 코드 상태를 결합합니다. 검사 증거는 실행 체크아웃과 연결 작업 트리 식별자에 묶습니다. 생성 문서는 모든 관련 저장소 밖인 `~/Documents/docs/`에 저장합니다.

v7 실행은 시작 커밋을 영역별로 보관합니다. 소스를 먼저 커밋해야 하는 작업에서는 그 커밋 뒤에 검사·검토·QA 증거를 새 코드 상태로 만들고 최종 게이트를 통과시킵니다. `review_brief.py`는 시작 커밋을 기준으로 커밋된 변경 파일도 검토자에게 보여줍니다. 게이트 뒤에는 Git 상태를 바꾸지 않은 채 푸시와 로컬 설치본을 확인합니다.

`scripts/orchestrate.py`는 작업 범위에 맞는 세 검토·QA 역할, 슬롯별 관점·담당 질문과 선행 관계를 출력합니다. 설계 검토는 요구사항·수용 기준, 구조·호환성, 오류·보안·검증 가능성으로, 코드 검토는 설계 일치·회귀, 정확성·보안, 테스트·유지보수로, QA는 정상 흐름, 경계·오류·권한, 회귀·운영 환경으로 나눕니다. `scripts/workflow_harness.py`는 대상 저장소 밖의 작업 폴더를 만들고 세 개별 결과, 설계 승인, 현재 코드에 대한 검증·QA 게이트를 확인합니다. 실제 서브에이전트 생성과 테스트 수행은 Codex가 스킬에 따라 진행합니다. 기존 v2·v3 작업 기록은 이전 CLI와 게이트로 계속 확인합니다.

v4 이후 실행은 설계 게이트 전에 로컬 `verification-plan.json`과 `qa-plan.json`을 작성합니다. 첫 파일에는 실제 저장소에서 확인한 검사 명령과 필수 여부를, 둘째에는 각 슬롯의 시나리오와 설계의 수용 기준 ID를 기록합니다. 설계의 `## 수용 기준`에 선언한 모든 적용 기준이 담당 영역의 QA 시나리오에 연결되어야 합니다. 설계·계획이 변경되면 설계 게이트를 다시 통과해야 합니다. 필수 테스트가 통과해야 코드 검토 결과를 기록하고, 세 코드 검토가 통과해야 QA 시나리오 결과를 기록합니다. 최종 게이트는 모든 필수 검사와 계획된 QA 시나리오의 PASS, 로컬 증거 파일의 내용·해시, 현재 설계·코드 상태를 확인합니다.

실패한 검사나 QA 시나리오는 원인·조치 근거를 남긴 뒤 같은 ID를 새 증거로 다시 실행해야 합니다. 환경 복구나 에이전트의 설명만으로 통과 처리하지 않습니다. 브라우저·실기기가 없어 QA를 실행하지 못했다면 미검증으로 보고하고 완료로 표시하지 않습니다. 하네스 사용법과 현재 CLI 예시는 [서브에이전트 운영과 하네스](skills/implementation-workflow/references/orchestration.md)에 있습니다.

신규 실행은 단계별 지침만 읽습니다: [요청·설계](skills/implementation-workflow/references/intake-design.md), [구현·검사](skills/implementation-workflow/references/implementation-check.md), [코드 검토·QA](skills/implementation-workflow/references/review-qa.md). `review_brief.py`는 원문·설계·계획·변경 경로를 링크하는 짧은 브리프를 만들고, 오케스트레이터는 슬롯별 추천 모델·추론 강도를 출력합니다. `run_check.py`는 계획된 검사 명령의 로그와 종료 코드가 담긴 manifest를 시도별로 보관합니다. v5 이후 `check-record`는 `--manifest-file`을 필수로 받아 계획 명령·체크아웃·코드 해시와 PASS/FAIL이 실제 실행에 맞는지 검증합니다. 이전 시도는 당시 계획 명령과 묶어 보존합니다. `evidence_summary.py`는 그 증거를 짧게 보여주며 판정을 바꾸지 않습니다.

v5 이후 설계·코드 검토 및 QA 결과는 `render_result.py`에 JSON 원본을 주어 개별 Markdown과 세 결과 통합 문서를 생성합니다. JSON에는 `schema_version: 1`, `stage`, `area`, `slot`, `agent_id`, 슬롯의 `focus`, `result`, `findings: []`, `evidence: []`가 필요합니다. 발견 사항에는 고유 `id`, `location`, `observation`, `impact`, `correction`, `reproduction`을 적습니다. 각 슬롯의 `review`/`agent-result` 기록에는 `--result-json`과 실제 `--model`, `--effort`, `--model-reason`, `--context-mode`, `--context-reason`을 지정합니다. 대화 상속 해제를 지원하지 않는 호스트에서는 전체 문맥으로 전환하고 이유를 남깁니다. 원본 JSON과 생성 Markdown의 불일치·변조는 게이트가 거부합니다. 다시 렌더링할 때 이전 Markdown은 시도별 archive로 보관됩니다.

두 영역을 동시에 작업하는 v5 실행에서는 선택적 `impact-map.json`으로 변경 파일의 소유 범위를 적을 수 있습니다. `frontend`, `backend`, `shared` 각각에 `{"glob":"frontend/**","reason":"화면 소유"}` 형태의 목록을 둡니다. 루트·하위 `contracts/`, `shared/`, `schema/`, `schemas/`와 공통 설정·lockfile은 이 지도와 무관하게 공유로 처리합니다. 모호하거나 미분류된 파일도 양쪽 검증을 무효화합니다. 지도가 없으면 기존 전체 코드 해시를 사용하고, 잘못된 지도는 설계 게이트가 거부합니다. v2~v4 실행의 게이트 의미는 그대로 유지합니다.

질문 UI가 해당 환경과 질문 유형에 제공되면 선택지와 자유 입력을 우선 사용합니다. `질문 답하기`와 같은 버튼의 표시 방식은 Codex 클라이언트가 결정합니다. 스킬과 훅은 버튼을 직접 생성하거나 기록된 서브에이전트 ID의 실존성을 검증하지 못합니다.

설계·검토·QA 리포트는 기본적으로 `~/Documents/docs/<저장소>/<브랜치>/<작업 항목>/<실행>` 폴더에 저장합니다. 작업 항목을 지정하지 않으면 브랜치 아래에 실행 폴더를 만듭니다. 같은 작업의 미완료 실행은 재사용하고, 명시적으로 새 실행을 시작할 때만 새 폴더를 만듭니다. 기존에 사람이 만든 폴더는 현재 작업의 폴더임을 확인한 뒤 `--parent-dir`로 지정할 수 있습니다. 관련 Git 저장소 내부 경로는 거부합니다. 기존 `~/.codex/implementation-workflow-runs/`의 v2·v3 실행 기록은 후속 명령에서 계속 읽을 수 있지만 새 실행은 그곳에 만들지 않습니다. 플러그인은 문서를 업로드하지 않으며 `~/Documents`의 운영체제 클라우드 동기화 설정까지 제어하지는 않습니다. `scripts/guard_documents.py`는 Git 변경에서 `README.md`를 제외한 문서 파일을 검사합니다. Markdown, 텍스트, PDF, Word 등 일반 문서 확장자와 `docs/`·`documentation/`·`design-docs/`·`reports/` 아래 파일을 차단합니다.

Git 훅은 개발자 컴퓨터에서 우회할 수 있으므로 원격 푸시·병합 금지를 완성하려면 GitHub 저장소 또는 조직의 push ruleset과 필수 상태 검사가 필요합니다. GitHub 원격 규칙은 이 플러그인 설치만으로 자동 배포되지 않습니다.

`assets/document-guard.yml`은 PR 및 merge queue용 필수 상태 검사 템플릿입니다. 각 대상 저장소의 기본 브랜치에 설치하고 `document-guard`를 필수 검사로 설정해야 병합을 막습니다. private/internal 저장소의 모든 브랜치 푸시를 서버에서 거절하려면 문서 경로 제한과 `README.md` 예외를 둔 GitHub push ruleset을 추가로 설정해야 합니다. 사용자 계정·조직이 다른 저장소까지 하나의 설정으로 보호할 수는 없습니다.

이 저장소에는 사용자가 직접 게시를 요청한 스킬 원본이 들어갑니다. 실제 구현 작업에서 생성되는 설계·검토·QA 결과 문서는 Git 저장소에 넣지 않습니다. 현재 개발 브랜치의 플러그인을 설치할 때는 저장소를 Codex 마켓플레이스로 추가하고 `implementation-workflow`를 선택합니다. 자동 라우팅 훅은 설치 후 신뢰 설정이 필요할 수 있습니다.

검증: `python3 -m unittest discover -s tests -v`
