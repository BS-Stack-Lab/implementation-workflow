# Implementation Workflow

문서 기반 코드 구현을 위한 Codex 스킬, `UserPromptSubmit` 자동 라우팅 훅, 로컬 단계 검증 하네스와 서브에이전트 작업 그래프입니다. 새 구현 요청마다 설계 검토 후 바로 구현할지 사용자가 직접 설계를 검토할지 질문 UI로 묻습니다. 동일 작업을 이어갈 때는 기존 실행의 선택과 상태를 사용합니다. 단일 영역의 작고 국소적인 작업은 설계·코드 검토와 QA에 각 1명, 위험하거나 영향이 불명확한 작업과 두 영역 작업은 각 3명을 배정합니다. 필수 검사·사용자 설계 변경 승인·QA 증거 게이트는 두 모드 모두 유지합니다. 작업당 Pro 주간 사용량 1% 미만은 계정의 정수 퍼센트 지표와 공유 창 때문에 보장할 수 없으며, 측정값과 한계를 보고합니다.

## 중단 후 이어하기와 다중 저장소

`init --work-item <작업-ID>`는 같은 작업의 미완료 로컬 실행을 찾아 재사용합니다. 새 실행이 필요하면 `--new-run`을 명시합니다. `workflow_harness.py status --run-dir <실행-폴더>`는 `stage`, `next_action`, `blocker`, `complete`를 JSON으로 보여줍니다. `orchestrate.py --run-dir <실행-폴더>`는 그 상태와 해당 실행의 작업 그래프를 함께 출력합니다. 설계 게이트가 통과한 `immediate` 구현 요청은 같은 작업에서 구현·테스트·코드 검토·QA·최종 보고를 계속합니다. `user-review`는 사용자의 설계 확인을 기다리고, 설계 문서만 요청한 작업은 설계 전달에서 끝납니다.

프론트엔드와 백엔드가 다른 Git 저장소면 `init --scope both --frontend-repo <FE-저장소> --backend-repo <BE-저장소>`로 각각 지정합니다. 같은 저장소를 두 인수에 줄 수도 있습니다. `--integration-repo`를 지정하지 않으면 백엔드 저장소에서 연동 검사를 실행합니다. 별도 통합 저장소를 지정하면 연동 게이트는 FE·BE·통합 저장소 세 곳의 코드 상태를 결합합니다. 검사 증거는 실행 체크아웃과 연결 작업 트리 식별자에 묶습니다. 생성 문서는 모든 관련 저장소 밖인 `~/Documents/docs/`에 저장합니다.

v7 이상 실행은 시작 커밋을 영역별로 보관합니다. 소스를 먼저 커밋해야 하는 작업에서는 그 커밋 뒤에 검사·검토·QA 증거를 새 코드 상태로 만들고 최종 게이트를 통과시킵니다. `review_brief.py`는 시작 커밋을 기준으로 커밋된 변경 파일도 검토자에게 보여줍니다. 게이트 뒤에는 Git 상태를 바꾸지 않은 채 푸시와 로컬 설치본을 확인합니다. 미완료 v2~v7 실행은 정확히 선택해 재개할 때 v8로 승격하고 이전 미결합 PASS를 다시 확인합니다. 완료된 구형 실행은 과거 기록으로 보존합니다.

`scripts/orchestrate.py`는 작업 범위에 맞는 세 검토·QA 역할, 슬롯별 관점·담당 질문과 선행 관계를 출력합니다. 설계 검토는 요구사항·수용 기준, 구조·호환성, 오류·보안·검증 가능성으로, 코드 검토는 설계 일치·회귀, 정확성·보안, 테스트·유지보수로, QA는 정상 흐름, 경계·오류·권한, 회귀·운영 환경으로 나눕니다. `scripts/workflow_harness.py`는 대상 저장소 밖의 작업 폴더를 만들고 세 개별 결과, 설계 승인, 현재 코드에 대한 검증·QA 게이트를 확인합니다. 실제 서브에이전트 생성과 테스트 수행은 Codex가 스킬에 따라 진행합니다. 기존 v2·v3 작업 기록은 이전 CLI와 게이트로 계속 확인합니다.

신규 v8 실행은 구현 전에 사용 중인 의존성 버전에 맞는 공식 문서/API 레퍼런스를 `official-sources.json`에 기록하고 설계·검증·리뷰 증거에 결합합니다. 실행기는 manifest 형식을 검사하며, 코디네이터와 검토자가 저장소에서 감지한 버전과 출처 버전이 맞는지 확인합니다. 공식 근거가 적용되는데 확인되지 않았거나 출처 매니페스트가 `pending`이면 설계 게이트를 통과하지 못합니다. 범위 밖 개선은 질문으로 기록합니다. `scope_extension`은 비차단이므로 거절되거나 답이 없으면 본 작업을 계속하고 해당 이슈는 최종 보고에 남깁니다. `required_decision`은 답에 의존하는 작업만 막고, 미해결 상태에서는 최종 결과를 `incomplete`로 게시합니다. `report-publish`는 변경 경로·코드 위치·동작 원리·검증·질문 결과가 들어간 로컬 초안을 현재 실행 상태에 원자적으로 결합합니다.

v4 이후 실행은 설계 게이트 전에 로컬 `verification-plan.json`과 `qa-plan.json`을 작성합니다. 첫 파일에는 실제 저장소에서 확인한 검사 명령과 필수 여부를, 둘째에는 각 슬롯의 시나리오와 설계의 수용 기준 ID를 기록합니다. 설계의 `## 수용 기준`에 선언한 모든 적용 기준이 담당 영역의 QA 시나리오에 연결되어야 합니다. 설계·계획이 변경되면 설계 게이트를 다시 통과해야 합니다. 필수 테스트가 통과해야 코드 검토 결과를 기록하고, 세 코드 검토가 통과해야 QA 시나리오 결과를 기록합니다. 최종 게이트는 모든 필수 검사와 계획된 QA 시나리오의 PASS, 로컬 증거 파일의 내용·해시, 현재 설계·코드 상태를 확인합니다.

실패한 검사나 QA 시나리오는 원인·조치 근거를 남긴 뒤 같은 ID를 새 증거로 다시 실행해야 합니다. 환경 복구나 에이전트의 설명만으로 통과 처리하지 않습니다. 브라우저·실기기가 없어 QA를 실행하지 못했다면 미검증으로 보고하고 완료로 표시하지 않습니다. 하네스 사용법과 현재 CLI 예시는 [서브에이전트 운영과 하네스](skills/implementation-workflow/references/orchestration.md)에 있습니다.

신규 실행은 단계별 지침만 읽습니다: [요청·설계](skills/implementation-workflow/references/intake-design.md), [구현·검사](skills/implementation-workflow/references/implementation-check.md), [코드 검토·QA](skills/implementation-workflow/references/review-qa.md). `review_brief.py`는 원문·설계·계획·변경 경로를 링크하는 짧은 브리프를 만들고, 오케스트레이터는 슬롯별 추천 모델·추론 강도를 출력합니다. `run_check.py`는 계획된 검사 명령의 로그와 종료 코드가 담긴 manifest를 시도별로 보관합니다. v5 이후 `check-record`는 `--manifest-file`을 필수로 받아 계획 명령·체크아웃·코드 해시와 PASS/FAIL이 실제 실행에 맞는지 검증합니다. 이전 시도는 당시 계획 명령과 묶어 보존합니다. `evidence_summary.py`는 그 증거를 짧게 보여주며 판정을 바꾸지 않습니다.

v5 이후 설계·코드 검토 및 QA 결과는 `render_result.py`에 JSON 원본을 주어 개별 Markdown과 세 결과 통합 문서를 생성합니다. JSON에는 `schema_version: 1`, `stage`, `area`, `slot`, `agent_id`, 슬롯의 `focus`, `result`, `findings: []`, `evidence: []`가 필요합니다. 발견 사항에는 고유 `id`, `location`, `observation`, `impact`, `correction`, `reproduction`을 적습니다. 각 슬롯의 `review`/`agent-result` 기록에는 `--result-json`과 실제 `--model`, `--effort`, `--model-reason`, `--context-mode`, `--context-reason`을 지정합니다. 대화 상속 해제를 지원하지 않는 호스트에서는 전체 문맥으로 전환하고 이유를 남깁니다. 원본 JSON과 생성 Markdown의 불일치·변조는 게이트가 거부합니다. 다시 렌더링할 때 이전 Markdown은 시도별 archive로 보관됩니다.

두 영역을 동시에 작업하는 v5 실행에서는 선택적 `impact-map.json`으로 변경 파일의 소유 범위를 적을 수 있습니다. `frontend`, `backend`, `shared` 각각에 `{"glob":"frontend/**","reason":"화면 소유"}` 형태의 목록을 둡니다. 루트·하위 `contracts/`, `shared/`, `schema/`, `schemas/`와 공통 설정·lockfile은 이 지도와 무관하게 공유로 처리합니다. 모호하거나 미분류된 파일도 양쪽 검증을 무효화합니다. 지도가 없으면 기존 전체 코드 해시를 사용하고, 잘못된 지도는 설계 게이트가 거부합니다. v2~v4 실행의 게이트 의미는 그대로 유지합니다.

질문 UI가 해당 환경과 질문 유형에 제공되면 선택지와 자유 입력을 우선 사용합니다. `질문 답하기`와 같은 버튼의 표시 방식은 Codex 클라이언트가 결정합니다. 스킬과 훅은 버튼을 직접 생성하거나 기록된 서브에이전트 ID의 실존성을 검증하지 못합니다.

설계·검토·QA 리포트는 기본적으로 `~/Documents/docs/<저장소>/<브랜치>/<작업 항목>/<실행>` 폴더에 저장합니다. 작업 항목을 지정하지 않으면 브랜치 아래에 실행 폴더를 만듭니다. 같은 작업의 미완료 실행은 재사용하고, 명시적으로 새 실행을 시작할 때만 새 폴더를 만듭니다. 기존에 사람이 만든 폴더는 현재 작업의 폴더임을 확인한 뒤 `--parent-dir`로 지정할 수 있습니다. 관련 Git 저장소 내부 경로는 거부합니다. 기존 `~/.codex/implementation-workflow-runs/`의 v2·v3 실행 기록은 후속 명령에서 계속 읽을 수 있지만 새 실행은 그곳에 만들지 않습니다. 플러그인은 문서를 업로드하지 않으며 `~/Documents`의 운영체제 클라우드 동기화 설정까지 제어하지는 않습니다. `scripts/guard_documents.py`는 저장소 루트의 `README.md`만 허용하고 Git 변경에서 알려진 문서 확장자와 `docs/`·`documentation/`·`design-docs/`·`reports/` 아래 파일을 차단합니다. 하위 폴더의 `README.md`는 예외가 아닙니다.

로컬 문서 guard를 Git 훅으로 연결하려면 각 대상 저장소에서 `git -C <저장소> config --show-origin --show-scope --get-all core.hooksPath`와 `git -C <저장소> rev-parse --git-path hooks`를 확인합니다. Git은 선택된 한 디렉터리에서 훅을 찾고 상대 경로는 비 bare 저장소의 작업 트리 루트 기준으로 해석합니다([Git hooks](https://git-scm.com/docs/githooks), [Git `core.hooksPath`](https://git-scm.com/docs/git-config#Documentation/git-config.txt-corehooksPath)). system/global/local/worktree 모든 범위에 설정이 없을 때만 `git -C <저장소> config --local core.hooksPath <플러그인 경로>/git-hooks`를 실행합니다. 하나라도 설정돼 있으면 해당 설정과 기존 hook 파일을 그대로 둡니다. 현재 유효 경로가 플러그인의 `git-hooks`가 아니면 이 저장소에서 document guard가 자동 실행되지 않습니다. 설치 과정은 local override, 기존 hook 교체, chaining을 자동으로 하지 않습니다. 기존 hook과 guard를 함께 연결하려면 저장소 또는 Git 관리자가 두 hook의 실행 순서와 인수·stdin 전달을 직접 구성해야 합니다. 이 설정은 저장소별이며 다른 저장소에 자동 적용되지 않습니다. Git 훅은 개발자 컴퓨터에서 우회할 수 있으므로 원격 푸시·병합 금지를 완성하려면 GitHub 저장소 또는 조직의 push ruleset과 필수 상태 검사가 필요합니다. GitHub 원격 규칙은 이 플러그인 설치만으로 자동 배포되지 않습니다.

`assets/document-guard.yml`은 PR 및 merge queue용 필수 상태 검사 템플릿입니다. 각 대상 저장소의 기본 브랜치에 설치하고 `document-guard`를 필수 검사로 설정해야 병합을 막습니다. GitHub 공식 문서에 따르면 private/internal 저장소의 push ruleset은 GitHub Team 플랜에서 사용할 수 있으며 활성화된 push ruleset의 포크 네트워크에도 적용됩니다. 문서 경로 제한과 저장소 루트 `README.md` 예외를 둔 ruleset을 추가하고 [GitHub 공식 지원 범위](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets)를 확인합니다. 사용자 계정·조직이 다른 저장소까지 하나의 설정으로 보호할 수는 없습니다.

이 저장소에는 사용자가 직접 게시를 요청한 스킬 원본이 들어갑니다. 실제 구현 작업에서 생성되는 설계·검토·QA 결과 문서는 Git 저장소에 넣지 않습니다. 현재 개발 브랜치의 플러그인을 설치할 때는 저장소를 Codex 마켓플레이스로 추가하고 `implementation-workflow`를 선택합니다. 자동 라우팅 훅은 설치 후 신뢰 설정이 필요할 수 있습니다.

설계 검토는 위험도에 맞춰 1명 또는 3명을 정하고, 역할별 짧은 브리프를 사용해 병렬 진행합니다. 검토 시간 목표는 대기와 재작업을 줄이기 위한 운영 기준이며, 시간이 지났다는 이유로 검토를 통과시키지 않습니다. 코드 주석은 제품·도메인 정책을 설명해야 하는 경우에만 추가합니다.

검증: `python3 -m unittest discover -s tests -v`
