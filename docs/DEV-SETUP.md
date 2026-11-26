# 개발 환경 설정

- **목적:** CI(`.github/workflows/ci.yml`)와 로컬이 항상 같은 결과를 내게 한다.
- **실제 설치·실행 절차는 [README.md](../README.md) "빠른 시작"에 있다** — uv 설치, DART API
  키, LLM provider 키까지 전부 거기 정리돼 있다. Claude Code로 작업할 때는
  [CLAUDE.md](../CLAUDE.md)를 먼저 읽을 것.

---

## 1. 고정된 버전

| 항목 | 버전 | 어디서 고정하는가 |
| --- | --- | --- |
| Python | 3.12 | `.python-version`, CI(`astral-sh/setup-uv`), `pyproject.toml`의 `requires-python` |
| uv | 0.12.5 | CI(`.github/workflows/ci.yml`)에 명시 |
| 의존성 | `uv.lock`에 정확한 버전 고정 | **직접 수정 금지**, `uv lock`/`uv add`로만 갱신 |

## 2. GitHub CLI 설치 (PR 작업용)

[BRANCHING.md](BRANCHING.md) "작업 흐름"의 `gh pr create`·`gh pr checks` 등은 GitHub CLI가
있어야 한다.

```bash
# macOS
brew install gh

# Windows (택1)
winget install --id GitHub.cli
scoop install gh

# Linux (Debian/Ubuntu 계열)
sudo apt install gh
```

그 외 OS·설치 방법은 [공식 설치 가이드](https://github.com/cli/cli#installation) 참고.

설치 후 로그인:

```bash
gh auth login
# ? What account do you want to log into?        GitHub.com
# ? What is your preferred protocol for Git?      HTTPS
# ? How would you like to authenticate?           Login with a web browser
```

확인:

```bash
gh auth status
```

## 3. 최초 설치

```bash
# uv 설치 (아직 없다면)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 저장소 루트에서
uv sync --dev

# pre-commit 훅 활성화 (커밋 시점에 lint/format 자동 실행)
uv run pre-commit install
```

## 4. 로컬에서 CI와 동일하게 검증하기

CI가 실행하는 것과 완전히 같은 명령이다.

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

`dart_integration` 마커 테스트(disclosure_lit)는 실제 DART OpenAPI를 호출하는 종단
테스트다 — `llm`/`slow`와 같이 기본 실행에서 제외된다(`addopts`). `DART_API_KEY`가
유효할 때 직접 돌려보려면:

```bash
uv run pytest -m dart_integration
```

키가 없거나 자리표시자면 `conftest.py`가 자동으로 skip한다.

pre-commit 훅을 설치해뒀다면 커밋 시점에 lint/format(포맷은 자동 수정까지)을 대신
잡아준다. 전체 파일에 대해 훅을 한 번에 돌려보려면:

```bash
uv run pre-commit run --all-files
```

## 5. 의존성을 추가/변경할 때

```bash
uv add <package>          # 일반 의존성
uv add --dev <package>    # 개발용 의존성(lint/test 도구 등)

# pyproject.toml을 직접 손으로 고쳤다면 반드시 재고정
uv lock
```

CI는 `uv sync --locked --dev`로 설치한다 — `pyproject.toml`과 `uv.lock`이 어긋나 있으면
설치 자체를 실패시킨다. 커밋에는 `pyproject.toml`과 `uv.lock`을 항상 같이 포함한다.

## 6. DART API 키

`DART_API_KEY`는 [opendart.fss.or.kr](https://opendart.fss.or.kr)에서 개인회원 가입 후
즉시 무료 발급된다. `.env`에 채워 넣으면 된다.

## 8. disclosure_lit 코퍼스 재현·공유

`data/raw/`(A10이 수집한 공시 원문·섹션)는 저작권 있는 공시 데이터라 git에 커밋하지
않는다(`.gitignore` 참고) — 용량도 실측 약 1.4GB(`sections.jsonl` 1GB + 원문 zip 캐시
400MB)라 커밋하면 클론이 계속 느려진다. 대신 두 가지 방법을 같이 쓴다.

### 8-1. 스크립트로 재현(권장, 용량 거의 0)

`data/raw/corpus_companies.json`(선정된 회사 목록, A10이 만든 메타데이터라 유일하게
git에 커밋돼 있다)을 그대로 두고, DART API로 직접 재수집한다:

```bash
# 1) A10 — 100개사(업종 층화표본) 대량 수집 — corpus_companies.json 이미 있으면 이 단계는 생략 가능
uv run python scripts/select_corpus_companies.py --target 100   # data/raw/corpus_companies.json 재생성(시드 고정, --seed 42 기본값)
uv run python scripts/build_corpus.py                            # 위 목록 기준 공시 수집(기본 2년치, lookback 6개월)

# 2) gold set(eval/gold/rumor_cases.jsonl) 14개사 — L2a 평가에 별도로 필요(100개사 표본과 겹치지 않음)
uv run python -c "
import json
codes = sorted({json.loads(l)['corp_code'] for l in open('disclosure_lit/eval/gold/rumor_cases.jsonl', encoding='utf-8') if l.strip()})
print(' '.join(codes))
"
# 위에서 나온 각 corp_code에 대해(실측 2025-06-01~수집일 정도면 27건 전부 커버):
uv run python scripts/collect_disclosures.py <corp_code> --bgn-de 20250601 --end-de <오늘YYYYMMDD>
```

`collect_disclosures.py`/`build_corpus.py` 둘 다 재실행 안전(이미 있는 `rcept_no`는
건너뜀)이라 몇 번을 다시 돌려도 안전하다. **주의**: `select_corpus_companies.py --seed 42`는
그 시점의 `corpCode.xml`(상장 종목 마스터)을 기준으로 샘플링하므로, `corpus_companies.json`
이 이미 커밋돼 있다면 그걸 그대로 쓰고(재실행하지 말고) `build_corpus.py`만 돌리는 게
정확히 같은 100개사를 보장한다 — DART 쪽 신규 상장·상장폐지가 있으면 재실행 결과가 달라질
수 있다. 같은 이유로 `build_corpus.py`의 날짜 범위(`--years`/`--lookback-months`)도
"오늘" 기준 상대값이라, 원래 수집 시점과 며칠 이상 차이 나면 경계에 걸친 공시 몇 건이
빠질 수 있다(실측 사례: PR #71 소급 재수집 때 경계에서 1건 빠짐 — `--bgn-de`/`--end-de`로
직접 날짜를 고정하면 피할 수 있다).

### 8-2. GitHub Release로 스냅샷 공유 (원본 비공개 저장소 전용)

> 공개 저장소에는 코퍼스 Release가 없다. 공개 저장소에서는 8-1로 직접 재현한다.

검증된 특정 시점의 코퍼스를 팀원 전체가 바이트 단위로 똑같이 받고 싶으면(재현 스크립트는
DART 쪽 데이터가 시간이 지나며 조금씩 달라질 수 있음), `data/raw/`를 zip으로 묶어
GitHub Release 첨부파일로 올린다 — private 레포라 팀원(리포 접근 권한자)만 내려받을 수
있다. 최신 릴리스는 저장소 Releases 탭에서 확인. 받은 뒤 압축을 `data/raw/`에 풀면
바로 쓸 수 있다(`corpus_companies.json`은 git에 이미 있으니 덮어써도 무방).
