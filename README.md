# 공시 리터러시 어시스턴트

커뮤니티에 도는 주식 루머를 **DART 전자공시와 대조해 사실만 보여주는** 도구입니다.
루머 문장을 붙여넣으면, 관련 공시를 찾아 **뒷받침하는 사실과 배치되는 사실을 같은 비중으로**
나열하고 각 사실마다 원문 근거를 연결합니다.

> **투자 판단은 하지 않습니다.** 매수·매도 추천, 목표가, 등급·신호등을 일절 출력하지 않습니다.
> "공시에 실제로 무엇이 적혀 있는가"까지만 다루며, 이는 자본시장법상 유사투자자문업을 피하기
> 위한 설계 제약이자 제품 포지션입니다.

LG CNS AI 캠퍼스 교과목 팀 프로젝트(3인)로 진행했습니다. 이 저장소는 팀의 비공개 개발
저장소에서 공개용으로 정리해 옮긴 스냅샷이라 커밋 이력이 이어지지 않으며, 문서 속
PR·이슈 번호(`#123` 등)는 그 비공개 저장소 기준입니다.

---

## 무엇을 푸는가

루머를 확인하려면 DART에서 해당 회사 공시를 찾아 수십 페이지 문서를 읽어야 합니다. 비용이
커서 대부분 포기하고 **커뮤니티의 해석을 그대로 믿게 됩니다.** 이 도구는 그 확인 비용을
몇 초로 낮추되, **해석은 대신 해주지 않습니다.** 사실과 원문을 주고 판단은 사용자에게 남깁니다.

## 어떻게 동작하는가

```text
루머 문장 + 종목
      │
      ├─ L0  DART 수집 · 서식 구조 기반 청킹
      ├─ L1  하이브리드 검색(임베딩 유사도 + 키워드 보너스 − 거대 섹션 감점)
      ├─ L2a 사실 추출 → 2차 LLM 근거 검증(groundedness guard) → 투자 조언 필터
      ├─ L2b 상투구 군집화 → 공시 유형 귀속 → 통계 검정
      ├─ L3  리포트 카드 조립
      └─ L4  Streamlit 데모
```

설계에서 특징적인 부분 셋입니다.

**검색은 정확 탐색입니다.** 근사 최근접(ANN) 대신 회사 내 전체 후보의 유사도를 구합니다.
최종 순위가 유사도만이 아니라 키워드 보너스까지 합산해 정해지므로, 후보를 미리 잘라내면
순위가 재현되지 않기 때문입니다. 규모가 커질 때를 위한 교체 지점은 인덱스 계층에 열어뒀습니다.

**투자 조언 필터의 경계를 실측으로 정했습니다.** 금지어를 임의로 고르지 않고, 코퍼스
82,876개 섹션을 전수 검사해 **원문에 0회 등장하는 표현만** 차단 대상으로 삼았습니다.
법정 위험고지 문구("주가가 하락할 가능성", 원문 75회)를 막으면 부정적 사실만 지워져
"긍정·부정 동일 비중" 원칙이 오히려 깨지기 때문입니다.

**가설이 반증되어 기능을 축소했습니다.** 아래 "검증과 한계" 참고.

---

## 검증과 한계

정직하게 적습니다. 아래 수치는 자랑이 아니라 **측정 결과**입니다.

| 지표 | 값 | 비고 |
| --- | --- | --- |
| 팩트체크 재현율 | **76.3%** (74.0~80.3%) | 골드셋 25건, 5회 반복 평균·범위. 목표 80% **미달** |
| Hallucination rate | **19.2%** (16.0~22.0%) | 2단 검증 후에도 잔존 |
| 화법통계 held-out 재현율 | **4.0%** | 아래 참고 |
| 응답시간 | 3~5초(준비된 상태) | 첫 조회는 캐시 상태에 따라 더 걸림 |

**화법통계의 원래 가설은 반증됐습니다.** "이런 표현 뒤에 이런 사건이 따른다"를 보여주려
했으나, 회사 단위 split-half 검증에서 재현율이 4.0%였습니다(군집 세밀도 전 구간에서 동일).
그래서 **예측 기능을 제거하고** 검증을 통과한 서술 통계만 남겼습니다 — 이 표현이 어떤 공시
유형에 속하는지, 해당 기업이 그 유형을 과거에 얼마나 제출했는지 등입니다. 화면 문구는
**과거 시제만** 사용하도록 테스트로 고정했습니다.

통계 처리에는 기저율 비교, 회사 단위 중복 제거(pseudoreplication 방지), 다중비교 보정
(Benjamini-Hochberg), 우측 절단(right-censoring) 처리를 적용했습니다.

### 알려진 한계

- 환각률이 금융 정보 서비스 기준에 못 미칩니다. 실패는 **검색이 근거를 찾지 못한 케이스에
  집중**돼 있습니다. 섹션이나 사실이 아예 없을 때의 기권(`status="abstained"`)은 구현돼
  있지만, 검색 점수가 낮을 때의 기권(`FACTCHECK_MIN_RELEVANCE_SCORE`)은 기본으로 꺼져
  있어 임계값 보정이 다음 과제입니다
- 배치 수집 구조라 **방금 게시된 공시는 반영되지 않습니다**
- 채점자(LLM judge)의 정확도를 사람 라벨과 대조해 검증하지 않았습니다
- 단어 겹침 판정이 형태소 분석 없이 부분 문자열 기반이라, 한국어 교착어 특성에서 오탐 여지가
  남아 있습니다
- 실제 서비스화 전에는 **법률 자문이 필요합니다**

---

## 실행

### 사전 준비

| 필요 | 받는 곳 |
| --- | --- |
| **uv** | <https://docs.astral.sh/uv> (Python 3.12는 uv가 자동 설치) |
| **DART API 키** (무료) | <https://opendart.fss.or.kr> |
| **LLM API 키** | OpenAI / Anthropic / Gemini 중 하나 (`LLM_PROVIDER`로 전환) |

### 설치

```bash
git clone https://github.com/jh3779/disclosure-literacy-assistant.git
cd disclosure-literacy-assistant
uv sync --dev --extra local   # 기본 임베딩(EMBED_BACKEND=local)에 sentence-transformers 필요
cp .env.example .env          # DART_API_KEY와 LLM 키, MODEL_MINI·MODEL_FINAL 채우기
```

`EMBED_BACKEND=openai`로 바꾸면 `--extra local`은 필요 없습니다(검증은 local 기준).

### 코퍼스 수집

**공시 원문은 저장소에 포함돼 있지 않습니다**(저작권). 직접 수집해야 합니다. 예외로
`tests/fixtures/dart/`에 파서 테스트용 원문 6건이 있으며, 담당자 연락처는 마스킹했고
MIT 라이선스 적용 대상이 아닙니다([해당 README](tests/fixtures/dart/README.md)).

```bash
uv run python scripts/build_corpus.py   # data/raw/corpus_companies.json의 기업 전체를 수집 + 청킹
```

`data/raw/corpus_companies.json`(선정된 기업 목록)만 커밋돼 있어 같은 표본으로 재현할 수
있습니다. **`select_corpus_companies.py`는 다시 실행하지 마세요** — 기존 목록에 새 기업을
덧붙여 저장해 표본이 달라집니다. 팩트체크 평가(`run_eval`)에 필요한 골드셋 기업의 추가
수집 절차는 [docs/DEV-SETUP.md 8-1절](docs/DEV-SETUP.md)에 있습니다.

### 데모 실행

```bash
uv run streamlit run disclosure_lit/demo/app.py --server.fileWatcherType none
```

첫 조회는 해당 기업 임베딩과 군집 인덱스를 만드느라 시간이 걸리고, 이후에는 디스크 캐시를
사용합니다.

### 검증

```bash
uv run pytest                     # 690여 건
uv run ruff check . && uv run ruff format --check .
uv run python -m disclosure_lit.eval.run_eval       # 팩트체크 평가(LLM 호출 발생)
uv run python -m disclosure_lit.eval.l2b_holdout    # 화법통계 holdout 검증
```

---

## 구조

```text
disclosure_lit/
  config.py        설정 로딩
  llm.py           LLM 호출 단일 진입점(provider 교체 가능)
  embed.py         임베딩(로컬 BGE-m3-ko 기본)
  cache_store.py   지문 기반 디스크 캐시(원자적 교체)
  dart/            L0  수집·청킹
  retrieval/       L1  종목별 검색 · 상투구 클러스터 인덱스
  factcheck/       L2a 클레임 추출 · 근거 검증 · 규제 필터
  dictionary/      L2b 군집화 · 의미 귀속 · 통계 · holdout
  report/          L3  리포트 조립
  demo/            L4  Streamlit 화면
  eval/            L5  골드셋 평가 · LLM judge

docs/              기획·설계·검증 기록
tests/             690여 건(계층 의존 방향 검사 포함)
```

`tests/test_layer_boundaries.py`가 **아래 계층이 위 계층을 import하지 못하도록** 강제합니다.
함수 내부 지연 import까지 검사하며, 아직 해소하지 못한 역방향은 목록에 명시해 두어 조용히
늘어나지 않게 했습니다.

## 문서

| 문서 | 내용 |
| --- | --- |
| [docs/PRD-MVP.md](docs/PRD-MVP.md) | 기획·주제 결정 경위 |
| [docs/DESIGN.md](docs/DESIGN.md) | 아키텍처·Phase·마일스톤 |
| [docs/testing/](docs/testing/) | 기능별 검증 기록 |
| [docs/discussion-history.md](docs/discussion-history.md) | 개발 과정의 쟁점·결정 기록(비공개 저장소 이슈·PR 요약) |
| [CONTRIBUTING.md](CONTRIBUTING.md) | 브랜치·리뷰 규칙 |

---

## 라이선스

[MIT License](LICENSE).

공시 원문은 이 저장소에 포함돼 있지 않으며, DART 이용 조건은 해당 서비스 정책을 따릅니다.
다만 `tests/fixtures/dart/`의 공시 XML 6건은 파서 테스트용으로 들어 있고(담당자 연락처
마스킹), **이 파일들에는 MIT 라이선스가 적용되지 않습니다.** 데모에 포함된 글꼴은 각 글꼴의
라이선스(OFL) 파일을 함께 두었습니다.
