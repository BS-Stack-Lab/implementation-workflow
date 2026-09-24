# Implementation Workflow

문서 기반 코드 구현을 위한 Codex 스킬, `UserPromptSubmit` 자동 라우팅 훅, 로컬 단계 검증 하네스와 서브에이전트 작업 그래프입니다. 매 새로운 구현 요청마다 설계 문서를 작성한 뒤 독립 검토를 거쳐 바로 구현할지, 사용자가 설계를 직접 검토한 뒤 구현할지 반드시 묻습니다. 이전 요청의 선택은 재사용하지 않습니다. 사용자 검토를 선택하면 로컬 설계 문서 링크를 제공하고 최종 설계 확인을 받을 때까지 구현을 기다립니다. 프론트엔드만 작업하면 프론트엔드 설계·QA 문서만, 백엔드만 작업하면 백엔드 설계·QA 문서만 작성합니다. 두 영역을 모두 작업하면 각 영역의 문서를 따로 작성하고 API·데이터 계약 검토와 연동 QA를 수행합니다. 모든 경우에 독립 서브에이전트 검토, 필요한 설계 변경 승인, 구현, 테스트, 코드 리뷰와 최종 보고를 순서대로 수행합니다.

`scripts/orchestrate.py`는 작업 범위에 맞는 역할과 선행 관계를 출력합니다. `scripts/workflow_harness.py`는 대상 저장소 밖의 작업 폴더를 만들고 설계 검토·사용자 승인·검증·QA 게이트를 확인합니다. 실제 서브에이전트 생성과 테스트 수행은 Codex가 스킬에 따라 진행합니다. 하네스 사용법은 `skills/implementation-workflow/references/orchestration.md`에 있습니다.

설계·QA 리포트는 기본적으로 `~/.codex/implementation-workflow-runs/` 아래 작업별 폴더에만 저장합니다. 이 폴더는 대상 Git 저장소 밖의 로컬 경로여야 합니다. `scripts/guard_documents.py`는 Git 변경에서 `README.md`를 제외한 문서 파일을 검사합니다. Markdown, 텍스트, PDF, Word 등 일반 문서 확장자와 `docs/`·`documentation/`·`design-docs/`·`reports/` 아래 파일을 차단합니다.

Git 훅은 개발자 컴퓨터에서 우회할 수 있으므로 원격 푸시·병합 금지를 완성하려면 GitHub 저장소 또는 조직의 push ruleset과 필수 상태 검사가 필요합니다. GitHub 원격 규칙은 이 플러그인 설치만으로 자동 배포되지 않습니다.

`assets/document-guard.yml`은 PR 및 merge queue용 필수 상태 검사 템플릿입니다. 각 대상 저장소의 기본 브랜치에 설치하고 `document-guard`를 필수 검사로 설정해야 병합을 막습니다. private/internal 저장소의 모든 브랜치 푸시를 서버에서 거절하려면 문서 경로 제한과 `README.md` 예외를 둔 GitHub push ruleset을 추가로 설정해야 합니다. 사용자 계정·조직이 다른 저장소까지 하나의 설정으로 보호할 수는 없습니다.

이 저장소에는 사용자가 직접 게시를 요청한 스킬 원본이 들어갑니다. 실제 구현 작업에서 생성되는 설계·검토·QA 결과 문서는 Git 저장소에 넣지 않습니다. 현재 개발 브랜치의 플러그인을 설치할 때는 저장소를 Codex 마켓플레이스로 추가하고 `implementation-workflow`를 선택합니다. 자동 라우팅 훅은 설치 후 신뢰 설정이 필요할 수 있습니다.

검증: `python3 -m unittest discover -s tests -v`
