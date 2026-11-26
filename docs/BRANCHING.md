# 브랜치 & 협업 규칙

- **상태:** `main` 브랜치 보호는 이미 GitHub에 적용되어 있다 — 아래 규칙은 초안이 아니라
  실제로 걸려 있는 제약이다(`gh api repos/jh3779/rag-project/branches/main/protection`으로 확인 가능).
- **팀 편성:** 3인(jh3779, powercodingman, 9abin). 2026-09-09부터 필수 승인 1건을
  실제로 GitHub 설정에 반영했다(아래).

---

## `main` 브랜치 보호 설정

- **직접 push 금지.** PR을 통해서만 병합 가능 — `enforce_admins=true`라 저장소 관리자도 예외 없음.
- **CI 통과 필수.** `.github/workflows/ci.yml`의 `markdown-lint`·`python` job이 모두 통과해야
  병합 버튼이 활성화된다.
- **타인의 리뷰 승인 1건 이상 필수(2026-09-09~).** `required_approving_review_count=1`.
  PR 작성자 본인은 GitHub 정책상 자기 PR을 승인할 수 없어 자동으로 "타인 승인"이
  강제된다. `dismiss_stale_reviews=true`라 새 커밋을 push하면 기존 승인은 무효화되고
  다시 승인받아야 한다.
- **병합은 PR 작성자가 진행한다.** 리뷰어는 승인까지만 하고, 승인 이후 병합 버튼을
  누르는 건 항상 PR을 연 작성자 본인이다 — 리뷰어가 대신 병합하지 않는다.
- **force-push·브랜치 삭제 금지.**
- **conversation resolution 필수.** PR에 달린 리뷰 코멘트를 전부 resolve해야 병합 가능.

## 브랜치 자동 정리

- **병합 시 자동 삭제.** 저장소 설정 `delete_branch_on_merge=true` — PR을 GitHub에서
  병합(merge 버튼/`gh pr merge`)하면 헤드 브랜치가 자동으로 삭제된다.
- **주기적 정리.** `.github/workflows/branch-cleanup.yml`이 매주 월요일(한국시간 낮 12시)
  `main`에 이미 병합된 브랜치를 찾아 자동 삭제한다 — `delete_branch_on_merge`가 못 잡는
  경우(예: `git merge`로 로컬에서 직접 병합 후 push한 브랜치)를 위한 보완책. 필요하면
  Actions 탭에서 수동 실행(`workflow_dispatch`)도 가능.

## 브랜치 네이밍

우지훈의 다른 저장소(`file-converter`, `lanecoach-ai`)에서 이미 쓰고 있는 패턴을 그대로 가져온 것 — 이 저장소만의 새 규칙이 아니다.

```text
feat/<짧은-설명>      # 새 기능
fix/<짧은-설명>       # 버그 수정
docs/<짧은-설명>      # 문서 작업
chore/<짧은-설명>     # 설정, 의존성, CI 등 기타 작업
```

설명은 케밥 케이스로 변경 내용을 직접 서술한다(티켓 관리 도구를 안 쓰므로 티켓 번호는 붙이지 않음).

## 작업 흐름

1. `main`에서 새 브랜치 생성 (최신 `main`을 pull한 뒤 분기)
2. 작업 진행 → 로컬에서 `docs/DEV-SETUP.md`의 검증 명령을 먼저 돌려본다
3. 커밋 후 push, `gh pr create`로 PR 생성
4. PR 본문 첫 줄 또는 마지막 줄에 리뷰어를 `@깃허브아이디`로 태그하고 "확인 요청드립니다"/"리뷰 요청드립니다" 문구를 넣는다
5. CI 통과 + 타인의 리뷰 승인 1건 확인 후 **PR 작성자 본인이** 병합

## AI 협업 시

Claude가 이 저장소에서 브랜치 작업이나 커밋을 진행하는 경우에도 위 규칙이 동일하게 적용된다.

- Claude는 **PR을 여는 것까지만** 하고 병합은 하지 않는다 — 병합은 그 PR을 요청한
  사람(작성자 본인)이 승인 확인 후 직접 한다.
- push 전에 `docs/DEV-SETUP.md`에 있는 로컬 검증 명령을 먼저 돌려서 CI 실패를 미리 걸러낸다.
