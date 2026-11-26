# 파일 네비게이션

레포 안의 모든 파일이 어디 있고 무엇을 하는 자리인지 보기 위한 지도. 프로젝트
전체 설명은 [README.md](../README.md), Claude Code로 작업할 때는
[CLAUDE.md](../CLAUDE.md), 설계·Phase·마일스톤은 [DESIGN.md](DESIGN.md) 참고.

## 기획/설정 문서 (`docs/`)

| 경로 | 내용 |
| --- | --- |
| [PRD-MVP.md](PRD-MVP.md) | 🔒 문제 정의·주제 결정 경위 기록 — 기준문서(잠금, `CONTRIBUTING.md` 규칙 6) |
| [DESIGN.md](DESIGN.md) | 🔒 전체 설계·아키텍처·Phase 및 마일스톤 — 기준문서(잠금) |
| [proposals/finance-pivot/](proposals/finance-pivot/) | 🔒 현재 주제 채택 전 검토한 개별 기획서 3종(경쟁 서비스·데이터 가용성 리서치 원본) — 기준문서(잠금, 과거 스냅샷) |
| [BRANCHING.md](BRANCHING.md) | 브랜치 보호 설정, 브랜치 네이밍, PR 작업 흐름, AI 협업 규칙 |
| [DEV-SETUP.md](DEV-SETUP.md) | uv 기반 개발 환경 설치·검증 방법(실제 절차는 README 참고) |
| NAVIGATION.md | 이 문서 |
| [discussion-history.md](discussion-history.md) | 비공개 개발 저장소의 이슈·PR 논의(2026-09-07~11-26)를 결정 중심으로 정리한 요약 기록 |
| [00_project_brief.md](00_project_brief.md) ~ [06_open_questions.md](06_open_questions.md) | discovery-interview 스펙 세트 — 요구사항(REQ-*)·화면(SCR-*)·플로우(FLOW-*)·데이터(ENT-*)·상태(STATE-*)·미해결 질문(OQ-*)/가정(ASM-*). 기준문서(PRD-MVP.md/DESIGN.md)를 추적 가능한 ID 체계로 재정리한 것이지 새 결정을 만든 문서가 아니다 |
| [07_visual_style.md](07_visual_style.md) | 디자인 스타일 확정(ColorScheme "Ledger Blue") — 왜 신호등 색을 안 쓰는지 포함 |
| [08_system_flow.md](08_system_flow.md) | RAG 파이프라인(로딩~평가)을 실제 파일·함수에 대입한 Mermaid 다이어그램, 구현/미구현 표시 |
| [design-system/](design-system/) | 초기 디자인 아카이브 5페이지(현행 화면은 02·03·05·07 문서 참고) + `ds.js`/`styles.css` |
| `RAG_프로젝트_사전기획서_양식.docx` / `_작성본.docx` / `_작성본.md` | 강사 배포 양식과 그 작성본(제출용) |

## 루트 문서

| 경로 | 내용 |
| --- | --- |
| [README.md](../README.md) | 프로젝트 개요, 빠른 시작, 트러블슈팅 |
| [CLAUDE.md](../CLAUDE.md) | Claude Code가 이 레포에서 작업할 때 참고할 컨텍스트(아키텍처·스택·규약·주의점) |
| [CONTRIBUTING.md](../CONTRIBUTING.md) | 협업 규칙 요약 — 상세는 `docs/BRANCHING.md` |

## 코드 (`disclosure_lit/`) — 현재 프로젝트

| 경로 | 역할 (레이어) | 담당 |
| --- | --- | --- |
| `config.py`, `__init__.py` | 공통 설정(.env 로딩) | — |
| `embed.py` | 임베딩(`embed_texts`/`embed_query`) — L1 검색과 L2b 화법 클러스터링이 공용. 백엔드는 DEC-006으로 local `dragonkue/BGE-m3-ko`(dim=1024) 확정 | A(이승노) |
| `dart/` | L0 — DART 수집·정규화(`client.py`, `ingest.py`) | A(이승노) |
| `retrieval/` | L1 — 종목별 검색(`company_index.py`) + 화법 클러스터 인덱스(`phrase_index.py`) | `company_index.py` A(인덱싱)/C(조회 연동), `phrase_index.py` C(우지훈) |
| `pipeline.py` | L1→L2a/L2b 오케스트레이션(`run_factcheck`/`run_dictionary_lookup`/`resolve_corp_code`) — Issue #32/DEC-005 확정 구현 | C(우지훈) |
| `factcheck/` | L2a — 클레임 추출·검증(`claim.py`, `verify.py`) | B(김가빈) |
| `dictionary/` | L2b — 상투구 클러스터링·통계(`cluster.py`, `stats.py`) | C(우지훈) — DEC-007로 정정 |
| `report/` | L3 — 팩트체크+화법통계 통합 카드(`card.py`) | C(우지훈) |
| `demo/` | L4 — Streamlit 데모(`app.py`) | C(우지훈) |
| `eval/` | L5 — gold set 스키마·평가 실행(`gold_schema.py`, `run_eval.py`) | C 수집, 전원 검수 |

**남은 `NotImplementedError` 스텁은 L2a와 L5뿐이다(2026-09-13 기준)** — L2a
`factcheck/`(B 담당)와 L5 `eval/run_eval.py`. **L2b(`dictionary/{cluster,stats}.py` +
`retrieval/phrase_index.py`, C 담당)는 2026-09-13에 구현 완료**(임베딩 유사도 기반
탐욕적 클러스터링 + 사후 이벤트 라벨 집계 — 실제 라벨 데이터는 아직 없어
`register_outcome_labels()`로 나중에 채워야 함). 그 외 L0 수집·L1 종목 검색·
`pipeline.py`·L3 리포트 카드·L4 데모는 구현돼 있고, L3/L4는 L2a가 없는 동안 샘플
폴백으로 화면을 보여준다.

이 문장은 병합될 때마다 낡으니 그대로 믿지 말 것 — 특정 함수가 실제로 동작하는지는
해당 파일에 `raise NotImplementedError`가 남아 있는지로 직접 확인하는 게 정확하다.

## 코드 (그 외)

| 경로 | 내용 |
| --- | --- |
| `tests/` | pytest — disclosure_lit 테스트는 평면 구조로 `tests/test_<domain>.py`(예: `test_dart_client.py`, `test_factcheck_verify.py`) 네이밍을 따른다 — 도메인별 하위 디렉터리 없음 |
| `tests/fixtures/dart/` | 실제 DART 원문 XML 6건(Family A 3 / Family B 3, 담당자 연락처 마스킹, MIT 라이선스 적용 제외) — 파서 회귀 탐지용. 무엇을 왜 골랐는지는 같은 폴더 `README.md` 참고 |
| `scripts/` | **현재 프로젝트**: `dart_smoke.py`(DART API 키·응답 확인), `collect_disclosures.py`(종목 하나의 공시를 `data/raw/`로 일괄 수집 — 핵심 로직 `collect_for_company()`는 A10 다종목 수집이 재사용), `form_type_coverage.py`(`normalize_form_type`의 "기타" 미분류 비율 측정 — 회귀 감시용). **A10 코퍼스 구축**(두 단계, 순서대로 실행): `select_corpus_companies.py`(코스피·코스닥 중 업종별 균등 표본 선정 → `data/raw/corpus_companies.json`) → `build_corpus.py`(선정 종목 전체 수집, 끊겨도 재실행하면 이어서 진행, 한 종목 실패는 건너뛰고 마지막에 모아 출력). |
| `data/` | `data/raw/`는 **현재 프로젝트**가 쓴다 — `<corp_code>/documents/<rcept_no>.zip`(원문 캐시), `<corp_code>/sections.jsonl`(청킹 결과), `corpus_companies.json`(A10 코퍼스 대상 종목 목록). `.gitignore`에 있어 커밋되지 않으므로 레포에서는 안 보인다. |

## 설정

| 경로 | 내용 |
| --- | --- |
| `pyproject.toml` | 프로젝트 정의, ruff·pytest 설정 |
| `uv.lock` | 의존성 정확한 버전 고정 — 직접 수정 금지, `uv lock`으로만 갱신 |
| `.python-version` | 3.12 |
| `.env.example` | 필요한 환경변수 목록(DART API 키, LLM provider, 임베딩 백엔드) — 복사해서 `.env`로 채울 것 |
| `.pre-commit-config.yaml` | 커밋 전 자동 검사(공백/EOF/시크릿 탐지 + ruff) |
| `.markdownlint.jsonc` | 마크다운 린트 설정(줄 길이 제한(MD013)만 비활성화) |
| `.github/workflows/ci.yml` | PR·push 시 필수 상태 체크(`markdown-lint`, `python` job) |
| `.github/workflows/branch-cleanup.yml` | 매주 `main`에 병합된 브랜치 자동 삭제(`docs/BRANCHING.md` "브랜치 자동 정리" 참고) |
| `.gitignore` | 코퍼스 원문·파생 데이터·Python 산출물·비밀값 제외 |
| `.gitattributes` | 텍스트 LF 정규화, 바이너리(`.dump`/`.npy`/`.sqlite` 등) 표시 |
