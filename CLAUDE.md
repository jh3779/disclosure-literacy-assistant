# CLAUDE.md

Claude Code가 이 레포에서 작업할 때 참고할 컨텍스트.

## 🔒 문서 잠금 (AI 에이전트 필독)

3인 팀(A/B/C) 전원이 하네스 엔지니어링형(Claude Code 등) 또는 바이브 코딩형으로 작업할
가능성이 높다. 아래 문서는 **기준문서(locked)** — 방향·설계·기획 결정을 기록한 것이라,
일반 코드처럼 에이전트가 임의로 고치면 팀이 합의한 내용이 조용히 드리프트한다.

- `docs/PRD-MVP.md`, `docs/DESIGN.md`
- `docs/proposals/finance-pivot/*.md` (채택 시점 스냅샷 — 사실 정정 외 수정 금지)
- 이 문서(`CLAUDE.md`)의 "이 프로젝트가 뭔가" · "아키텍처 (레이어)" · "주의점 (Gotchas)" 절
  (단, "현재 상태" 절은 세션마다 갱신되는 로그 성격이라 잠금 대상 아님)

**규칙**: 에이전트는 사용자가 명시적으로 요청하지 않는 한 위 문서를 수정하지 않는다. 내용에
오류·누락·구식 정보를 발견해도 바로 고치지 말고 먼저 사용자에게 알린다. 변경이 필요하면
사람 논의 후 별도 PR + 전원 리뷰(`CONTRIBUTING.md` 규칙 2/6)를 거친다.

## 이 프로젝트가 뭔가

**공시 리터러시 어시스턴트.** 커뮤니티에 도는 테마주 루머를 DART 공시로 대조해 사실만
나열하는 팩트체커(L2a) + 상투적 IR 화법이 과거 통계적으로 무엇을 의미했는지 보여주는
공시 국어사전(L2b)을 하나로 합친 도구. **시스템은 투자 판단을 절대 내리지 않는다** —
매수/매도 추천, 목표가, 등급·신호등은 일절 출력하지 않고 "공시에 실제로 뭐라고 쓰여
있는가 / 과거 패턴이 통계적으로 어땠는가"까지만 다룬다(자본시장법상 유사투자자문업 회피).

전체 기획·주제 결정 경위: **docs/PRD-MVP.md**. 아키텍처·Phase·마일스톤: **docs/DESIGN.md**.
후보안 검토 과정(경쟁 서비스·데이터 가용성 리서치 원본): **docs/proposals/finance-pivot/**.

## 아키텍처 (레이어)

```text
L5 평가 (횡단)    eval/ — 팩트체크 gold set·화법 클러스터 유의성
L4 데모 UI        demo/app.py (Streamlit, 입력창 하나)
L3 통합 리포트    report/card.py — L2a 결과에 L2b를 자동 주석으로 결합
L2b 화법통계      dictionary/{cluster,stats}.py — 상투구 클러스터링 + 사후 이벤트 통계
L2a 팩트체크      factcheck/{claim,verify}.py — 클레임 추출 + claim verification
L1 검색           retrieval/{company_index,phrase_index}.py
L0 수집           dart/{client,ingest}.py — DART OpenAPI
```

**규제 방어선이 프로젝트의 심장.** 새 기능은 전부 "사실 나열/통계까지만, 종합 판단 없음"
원칙을 지켜야 한다. `factcheck/verify.py::verify_claim`은 이전 프로젝트 narrative_qa의
groundedness guard 패턴(LLM 출력이 실제 근거 문서로 뒷받침되는지 검증)을 재사용한다.

## 스택 / 규약

- **패키지 매니저: uv**. `uv sync --dev`, `uv run python -m ...`, `uv run pytest`.
- **LLM**: `disclosure_lit/llm.py::complete(system, user, *, tier, schema)`(provider 스왑:
  openai/anthropic/gemini, `LLM_PROVIDER`). `.env`에 `MODEL_MINI`·`MODEL_FINAL`을 **반드시**
  지정한다 — 비워 두면 모델명이 빈 문자열이라 호출이 실패한다. 평가·데모 기준은 둘 다
  `gpt-4o-mini`(`.env.example`).
- **DART**: `disclosure_lit/dart/client.py::DartClient` — opendart.fss.or.kr, 무료 API 키
  즉시 발급.
- **임베딩**: `disclosure_lit/embed.py` — DEC-006으로 확정, `local`(`dragonkue/BGE-m3-ko`,
  1024차원) 기본, `openai` 백엔드 스왑 경로도 있음(아직 미검증). L1 검색과 L2b 화법
  클러스터링이 공용으로 쓴다.
- **린트: ruff** (line-length 100, E/F/I/UP/B). `uv run ruff check disclosure_lit`.
- **현재 상태**: L0~L5 전 레이어 실동작(2026-09-25 기준, 스텁 없음) — 자세한 내용은 아래
  "현재 상태" 절 참고.

## 팀 분담

| 파트 | 담당 | 범위 |
| --- | --- | --- |
| A | 이승노 (powercodingman) | DART 파이프라인, 과거 공시 사례 라벨링 |
| B | 김가빈 (9abin) | L2a 팩트체크 엔진 |
| C | 우지훈 (jh3779) | L1 조회 연동, **L2b 화법통계 엔진**, L3/L4 통합 UI, L5 gold set |

상세 Phase 분담은 `docs/proposals/finance-pivot/03-integrated-proposal.md` 11~12절.

> 2026-09-11 정정: 이 표가 최초 작성 당시(PR #17) L2b를 B로 잘못 옮겨 적어 원안(11절, "C가
> L3에서 L2b를 조립해야 하니 L2b도 C" 근거 명시)과 어긋나 있었다. 재협의 흔적 없음을
> 커밋 이력으로 확인 후 원안 기준으로 되돌림 — 상세 근거는 `.agent/DECISIONS.md` DEC-007.

## 주의점 (Gotchas)

1. **규제 방어선 위반 금지**: 등급·신호등·목표가·수익률 예측을 절대 만들지 않는다
   (`docs/proposals/finance-pivot/03-integrated-proposal.md` 7절).
2. **부정/긍정 사실 동일 비중**: 부정적 사실만 골라 나열하면 암묵적 매도 시그널로 읽힌다.
3. **표본 부족 클러스터**: 화법통계는 최소 표본 수(예: 3건) 미달 시 "유의하지 않음"을 명시.
4. **L2a/L2b 출력 스키마부터 합의**: B·C가 착수 전 인터페이스(`FactCheckResult`/
   `ClusterStats` 형태)를 먼저 정해야 L3 통합 작업이 병목 없이 진행된다.
5. **`narrative_qa/`는 이전 프로젝트**였다 — 공개 전환 때 레포에서 제거했고 git history에만
   남아 있다. 코드·문서의 narrative_qa 언급은 패턴 출처 기록이니, 없는 디렉터리를 찾지 말 것.

## 현재 상태 (2026-09-25)

최종발표 **2026-09-29**. 발표 준비 문서는 `docs/final-presentation-outline.md`, 진행 현황은
`docs/WBS.md`. 열린 PR 없음(#115~#122 병합), 열린 이슈는 #103 하나.

- **L0(수집)**: `dart/{client,ingest}.py` 실동작 — DART API 클라이언트, 서식별 청킹, 대량
  코퍼스 수집(A10). 코퍼스(114개사·82,876섹션)는 GitHub 릴리즈
  `corpus-snapshot-2026-09-16`의 zip을 `data/raw/`에 풀어 쓴다(`docs/DEV-SETUP.md` 8절).
- **L1(검색)**: `retrieval/company_index.py`(하이브리드 검색, 회사별 지연 임베딩 + 디스크
  캐시), `retrieval/phrase_index.py`(화법 클러스터 인덱스, FAISS `IndexFlatIP`) 실동작.
- **L2a(팩트체크)**: `factcheck/{claim,verify}.py` 실동작(PR #51·#114) + `factcheck/
  compliance.py` — LLM 출력의 투자 조언 표현을 결정적으로 거르는 규제 방어선(PR #115·#116).
  패턴은 "코퍼스 원문 적중 0회"로만 넓힌다(모듈 docstring).
- **L2b(화법통계)**: `dictionary/` — 후보 추출(`candidates.py`), 응집형 클러스터링
  (`cluster.py`, 2026-09-21), 사후 이벤트 라벨(`outcome_labels.py`), 통계·held-out
  (`stats.py`·`holdout.py`). "상투구가 미래 사후 이벤트를 예측한다"는 가설은 held-out
  재현율 4.0%로 **반증**돼, 화면은 예측 없는 서술형(`cluster_meaning.py` — 이 표현이 흔히
  나타나는 공시 유형)으로 재설계됐다.
- **L3(리포트)·L4(데모)**: `report/card.py`, `demo/app.py` — 레트로 '공시 단말기' UI(PR
  #117). 외부 배포 전 확인할 것: 조회 기록(`demo/history.py`)이 서버의 모든 방문자에게
  공유된다. (제3자 배경 이미지는 공개 준비 브랜치에서 CSS 그라데이션으로 교체했다.)
- **L5(평가)**: `eval/run_eval.py`(L2a gold set 25건 — 2026-09-25 gpt-4o-mini 5회 평균
  재현율 76.3%, hallucination 19.2%), `eval/l2b_holdout.py`(held-out 재현율 4.0%).
  한 번 잰 값은 회차마다 ±수%p 흔들리니 반복 측정값을 쓴다.
- **데모 응답시간**: `docs/testing/response-time.md` — 준비가 끝난 뒤 조회당 3~5초(목표
  10초 충족). 서버 재시작 후 첫 조회는 디스크 캐시(`data/cache/`, PR #121)가 있으면 약
  27초, 캐시가 비어 있으면 첫 계산에 약 4~5분 — 발표 전에 한 번 조회해 두는 게 안전하다.
- **운영 메모**: CI(`ci.yml`)는 ubuntu에서, `main` 대상 PR에만 돈다 — 스택 PR엔 검사가
  안 붙고, Windows 전용 문제(경로 구분자 등)는 로컬에서만 드러난다.
- 이전 프로젝트 narrative_qa는 공개 전환 준비(PR #133)에서 레포에서 제거했다 — 필요하면
  git history를 본다.
