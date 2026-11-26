"""Streamlit 데모 — L4. 담당: C(우지훈).

입력창 하나로 루머/공시 문장을 받아 팩트체크(L2a)·화법통계(L2b)를 자동 분기해 L3 리포트
카드(report/card.py::build_report)를 렌더링한다.

L2a 엔진(B 담당, factcheck/verify.py)은 아직 구현 전(NotImplementedError)이다. L2b
(dictionary/{cluster,stats}.py, C 담당)는 2026-09-13 구현 완료 — `data/raw/`에 로컬
코퍼스(scripts/collect_disclosures.py로 수집)가 있으면 실제 문구로 클러스터를 만든다.
엔진이 없거나 인덱스가 비어 결과가 없을 때는 같은 카드 레이아웃을 샘플 데이터로 보여준다.

사후 이벤트 라벨(A12, scripts/label_post_events.py) 배선 — **2026-09-21부터 사실상
비활성**: `dictionary/stats.py::register_outcome_labels()`는 원래 `_outcome_labels()`
가 `_phrase_index()`와 같은 캐시된 인덱스 인스턴스로 라벨을 매칭해 채우는 훅이었다
(클러스터 ID가 그 순간의 인덱스 인스턴스에 종속되므로, 같은 부팅 과정 안에서 같은
인스턴스로 등록해야 안전하다는 원칙은 여전히 유효). 그런데 이 계산(코퍼스 전체
177,584개 문구 매칭)이 만드는 `ReportCard.phrase_stats`를 `main()`은 2026-09-17부터
렌더링하지 않고 있었다 — 아무도 안 보는 값을 위해 콜드 부팅마다 수십 분~2시간대
계산을 돌리고 있었다는 뜻이라(라이브 테스트로 확인), `_phrase_bundle()`이 이제 항상
빈 라벨을 반환한다(`_phrase_bundle()` docstring 참고). `build_outcome_labels()`
함수 자체와 단위 테스트는 남아있어, 기능이 부활하면 한 줄만 되돌리면 된다.

**2026-09-17/18 L2b 재설계**: 상투구 클러스터가 "미래 사후 이벤트"를 예측한다는 원래
가설(`card.phrase_stats`, `run_dictionary_lookup`)은 held-out 검증에서 반증됐다
(클러스터 세밀도를 0.35~0.75 전 구간에서 조정해도 재현율 0%). 그래서 `main()`은 그
렌더링을 더는 부르지 않고, 대신 "사후이벤트"를 **미래 사건이 아니라 그 표현이 원래
속한 공시 유형(form_type) 자체**로 재정의한 `_build_meaning_snapshot()`으로 대체했다 —
①이 표현이 흔히 나타나는 공시 유형(`dictionary/cluster_meaning.py`, 순수 분류, 예측
아님), ②이 유형이 과거 코퍼스에서 반복돼 온 정도(`dictionary/recurrence.py`, 여러
무작위 분할에 걸쳐 안정적으로 재현된 것만, **과거 시제로만 서술** — "반복될 것"이
아니라 "반복돼 왔다"), ③이 회사 자신의 최근 공시 흐름(`dictionary/company_trend.py`,
그 회사 과거 대비 비교일 뿐 타사 일반화 없음). `ReportCard.phrase_stats`/
`run_dictionary_lookup` 자체는 report/card.py·pipeline.py에 남아있다 — 스키마를
바꾸는 건 더 큰 범위의 별도 작업.

**2026-09-18 사용성 개선**: `@st.cache_resource`에 사용자 친화적 `show_spinner`
문구를 지정한다 — 기본값이면 Streamlit이 "Running _phrase_bundle()." 처럼 내부
함수 이름을 그대로 노출해서 실사용자가 보기엔 부적절하다. 팩트체크 엔진(B 담당)이
실구현된 뒤로는 사이드바의 "샘플 보기" 토글도 일반 사용자에게 혼란만 주므로 접어둔
"개발자 옵션" 아래로 옮겼다(`main()` 참고).
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

import streamlit as st

from disclosure_lit import cache_store
from disclosure_lit.config import settings
from disclosure_lit.dart.client import CorpCodeIndex, DartClient
from disclosure_lit.dart.ingest import DisclosureSection
from disclosure_lit.demo.history import (
    HISTORY_LIMIT,
    HistoryEntry,
    LookupResult,
    MeaningSnapshot,
    get_history,
)
from disclosure_lit.demo.terminal import (
    prepare_terminal,
    render_bezel,
    render_power_button,
    render_printer_slot,
)
from disclosure_lit.dictionary.candidates import (
    extract_phrase_candidates,
    load_all_local_sections,
    load_corpus_companies,
    load_sections_jsonl,
    select_clustering_pool,
)
from disclosure_lit.dictionary.cluster_meaning import ClusterMeaning, compute_cluster_meanings
from disclosure_lit.dictionary.company_trend import CompanyTrendItem, compute_company_trend
from disclosure_lit.dictionary.glossary import GLOSSARY
from disclosure_lit.dictionary.recurrence import (
    RecurrenceSignal,
    build_form_type_labels,
    compute_recurrence_signals,
)
from disclosure_lit.dictionary.stats import (
    OutcomeLabel,
    load_post_event_labels,
    register_outcome_labels,
)
from disclosure_lit.factcheck.verify import FactCheckItem, FactCheckResult
from disclosure_lit.report.card import ReportCard, build_report, resolve_claim
from disclosure_lit.retrieval.company_index import CompanyDisclosureIndex
from disclosure_lit.retrieval.phrase_index import PhraseCluster, PhraseClusterIndex

_logger = logging.getLogger(__name__)

_RAW_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "raw"
_CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "cache"

_SAMPLE_SECTION = DisclosureSection(
    rcept_no="00000000",
    corp_code="000000",
    form_type="유상증자",
    section_title="신주발행결정",
    text="당사는 운영자금 조달을 위해 500억원 규모의 제3자배정 유상증자를 결정하였습니다.",
)

_SAMPLE_FACT_CHECK = FactCheckResult(
    claim="(샘플) 이 회사가 유상증자로 자금을 조달한다는 소문이 있다",
    items=[
        FactCheckItem(
            fact="공시에 제3자배정 유상증자 500억원 결정 사실이 있습니다.",
            supporting_section=_SAMPLE_SECTION,
            polarity="positive",
        ),
        FactCheckItem(
            fact="발행가액·배정 대상자는 아직 확정 공시되지 않았습니다.",
            supporting_section=None,
            polarity="negative",
        ),
    ],
)

_KNOWN_POLARITIES = ("positive", "negative", "neutral")


@st.cache_resource(show_spinner="공시 코퍼스를 불러오는 중입니다...")
def _local_sections() -> list[DisclosureSection]:
    """`data/raw/<corp_code>/sections.jsonl` 전부를 모은다.

    `scripts/collect_disclosures.py`(A)로 수집한 게 없으면 빈 리스트 — 그 경우
    `_company_index()`/`_phrase_index()`는 예전처럼 빈 인덱스가 되어 동작은 그대로다.

    `show_spinner`에 사용자 친화적 문구를 직접 지정한다 — 기본값(`True`)이면
    Streamlit이 "Running _local_sections()." 처럼 내부 함수 이름을 그대로 노출한다
    (2026-09-18, 사용성 개선 — 실사용자에게 파이썬 함수명이 보이면 안 된다).
    """
    return load_all_local_sections(_RAW_DIR)


def _load_company_lazily(index: CompanyDisclosureIndex, corp_code: str) -> None:
    """`CompanyDisclosureIndex`의 `lazy_loader` 훅 — 검색이 실제로 요청한 회사
    하나만 그 자리에서 채운다(2026-09-18).

    이전엔 부팅 시 100개사 전체를 미리 임베딩해서 콜드스타트가 40.6분이었다(실측) —
    실제 사용자는 한 번에 회사 하나만 조회하는데도 나머지 99개사분을 매번 미리
    계산하고 있었다. 회사별 디스크 캐시(`cache_store.company_lazy 번들`)가 있으면
    그걸 재임베딩 없이 불러오고(`index_with_vectors`), 없으면 그 회사분만 새로
    임베딩(`index`)한 뒤 다음 조회를 위해 캐시에 남긴다 — 평균 회사당 ~24초
    (829섹션/회사 평균 기준, 40.6분/100개사)로 첫 응답 시간이 줄어든다.

    코퍼스에 그 회사가 없으면(로컬 수집 범위 밖) 아무것도 채우지 않는다 —
    `search()`가 빈 결과를 정상적으로 반환한다.
    """
    sections = [s for s in _local_sections() if s.corp_code == corp_code]
    if not sections:
        return

    fingerprint = cache_store.company_section_fingerprint(corp_code, sections)
    cached = cache_store.load_company_section_vectors(_CACHE_DIR, corp_code, fingerprint)
    if cached is not None:
        cached_sections, cached_vectors = cached
        index.index_with_vectors(cached_sections, cached_vectors)
        return

    index.index(sections)
    new_sections, new_vectors = index.dump_rows_for(corp_code)
    cache_store.save_company_section_vectors(
        _CACHE_DIR, corp_code, fingerprint, new_sections, new_vectors
    )


@st.cache_resource(show_spinner=False)
def _company_index() -> CompanyDisclosureIndex:
    """지연 로딩 인덱스 — 부팅 시 아무 회사도 미리 임베딩하지 않는다.

    `search()`가 처음 보는 corp_code를 요청하면 `_load_company_lazily()`가 그
    회사분만 그 자리에서 채운다(위 함수 docstring 참고). `@st.cache_resource`라
    이 인덱스 인스턴스 자체는 세션 내내 유지되므로, 한 번 조회한 회사는 이후
    요청에서 재임베딩·재로드 없이 즉시 응답한다.

    `show_spinner=False` — 이 함수 자체(빈 인덱스 생성)는 항상 즉시 끝난다. 실제
    지연 임베딩은 `search()` 안에서(이 함수 밖에서) 일어나므로, 그 구간은
    `main()`의 바깥쪽 `st.spinner()`가 사용자 친화적 문구로 감싼다.
    """
    return CompanyDisclosureIndex(lazy_loader=_load_company_lazily)


@st.cache_resource(show_spinner=False)
def _corp_code_index() -> CorpCodeIndex:
    """DART 전체 상장 종목명 -> corp_code 조회 인덱스.

    이전엔 빈 CorpCodeIndex([])라, extract_claim이 corp_name만 뽑고 corp_code는 못
    뽑는 정상 케이스(report/card.py::resolve_corp_code 참고)에서도 find_by_name이
    항상 실패했다 — "삼성전자 유상증자설"처럼 종목명으로만 된 루머는 데모의 핵심
    경로(루머 붙여넣기)인데도 실제로는 한 번도 종목을 못 찾고 화법 문구 단독 검색
    경로로 새는 상태였다.

    select_corpus_companies.py와 같은 corp_code.zip 캐시 경로를 재사용한다 — 이미
    받아둔 게 있으면 네트워크 없이 즉시 로드된다. DART_API_KEY가 없거나(로컬 데모
    실행 환경에 따라 있을 수도 없을 수도 있음) 네트워크 오류가 나면, 이 데모의 기존
    원칙(엔진 미비 시 크래시 대신 샘플로 대체)에 맞춰 빈 인덱스로 폴백한다 — 종목명
    해석만 안 될 뿐 나머지 데모 기능은 그대로 동작해야 한다.
    """
    if not settings.dart_api_key:
        return CorpCodeIndex([])
    try:
        client = DartClient(settings.dart_api_key)
        entries = client.fetch_corp_codes(cache_path=_RAW_DIR / "corp_code.zip")
    except Exception:  # noqa: BLE001 - 종목명 해석만 죽이고 데모 전체는 계속 동작
        # 2026-09-22(상세 감사) — 예전엔 이 폴백이 무로그였다. 인증 실패·네트워크
        # 오류·손상된 캐시 zip 중 뭐가 원인인지 구분할 방법이 없어 "종목명으로 루머를
        # 못 찾는다"는 증상만 남았다(cache_store.py의 "안전하게 폴백하되 항상 로그는
        # 남긴다" 관례와 다르게 여기만 빠져 있었다).
        _logger.warning(
            "corp_code 인덱스 로드 실패 — 종목명 해석은 빈 목록으로 폴백", exc_info=True
        )
        return CorpCodeIndex([])
    return CorpCodeIndex(entries)


@st.cache_resource(
    show_spinner="상투구 패턴을 처음 분석하고 있습니다(몇 분 정도 걸릴 수 있어요)..."
)
def _phrase_bundle() -> tuple[PhraseClusterIndex, list[OutcomeLabel]]:
    """`_phrase_index()`와 `_outcome_labels()`가 공유하는 **원자적** 캐시 단위.

    둘을 따로 캐싱하면(따로 디스크에 쓰고 따로 무효화하면) 서로 다른 시점에 빌드된
    `cluster_id`가 섞여 들어갈 위험이 있다 — 위 모듈 docstring "사후 이벤트 라벨 배선"
    참고, 상세 근거는 `cache_store.py` 모듈 docstring. 그래서 클러스터 인덱스 빌드와
    라벨 매칭을 이 함수 하나 안에서 끝내고, 디스크 캐시도 한 지문 아래 함께 쓰고
    읽는다(`cache_store.save_phrase_bundle`/`load_phrase_bundle`) — `_phrase_index()`/
    `_outcome_labels()`는 이 결과를 나눠주기만 하는 얇은 래퍼다.

    코퍼스가 없으면(collect_disclosures.py를 아직 안 돌렸으면) 예전과 동일하게
    빈 인덱스 + 빈 라벨(에러 아님).

    **2026-09-21 라벨 계산 건너뛰기(팀 리뷰 지적)**: `build_outcome_labels()`는
    코퍼스 전체(실측 177,584개 문구)를 `match_many()`로 매칭하는 무거운 단계라 콜드
    빌드 시간을 지배한다(위 클러스터 인덱스 빌드 자체는 수십~백여 초인데, 이 단계는
    수 분~수십 분). 그런데 이 결과(`OutcomeLabel`)는 `register_outcome_labels()`
    →`compute_stats()`→`ReportCard.phrase_stats`로만 이어지고, `main()`은
    2026-09-17부터 `card.phrase_stats`를 렌더링하지 않는다(`_build_meaning_snapshot()`은
    `card.fact_check`만 읽는다 — grep으로 확인) — 즉 이 계산의 결과물을 아무도 안
    본다. `phrase_stats`/`run_dictionary_lookup`/`compute_stats` 자체는 스키마
    변경 범위가 커서 그대로 남겨뒀지만(위 모듈 docstring 2026-09-17/18 절 참고),
    그걸 위해 매 콜드 부팅마다 이 계산을 실제로 돌릴 이유는 없다 — 항상 빈 라벨을
    돌려준다. `register_outcome_labels()`가 빈 리스트를 등록해도 `compute_stats()`는
    `sample_count=0`으로 안전하게 처리한다(크래시 아님). 이 기능이 나중에 부활하면
    (스키마 변경과 함께) `build_outcome_labels()` 자체는 그대로 있으니 이 한 줄만
    되돌리면 된다.
    """
    sections = _local_sections()
    if not sections:
        return PhraseClusterIndex(), []

    fingerprint = cache_store.phrase_fingerprint(sections, _RAW_DIR)
    cached = cache_store.load_phrase_bundle(_CACHE_DIR, fingerprint)
    if cached is not None:
        return cached

    index = PhraseClusterIndex()
    # 2026-09-21: 회사 수(min_companies) 필터는 더 이상 여기서 클러스터 생존 여부를
    # 정하지 않는다(팀 리뷰 지적) — 매칭은 표본 크기와 무관하게 항상 되고, 신뢰도
    # 판정만 `cluster_meaning.py`가 `MIN_COMPANIES_FOR_MEANING`으로 따로 매긴다.
    # `companies_by_phrase`는 여기서는 더 안 쓰지만, `select_clustering_pool()`
    # 자체는 규모 제한용 사전 필터(`LEXICAL_MIN_COMPANIES`)에 여전히 필요하다.
    pool, _companies_by_phrase = select_clustering_pool(sections)
    index.build(pool)
    labels: list[OutcomeLabel] = []  # 위 docstring 참고 — 아무도 안 읽는 값이라 계산 자체를 건너뜀
    cache_store.save_phrase_bundle(_CACHE_DIR, fingerprint, index, labels)
    return index, labels


@st.cache_resource(show_spinner=False)
def _phrase_index() -> PhraseClusterIndex:
    """`data/raw/` 로컬 코퍼스에서 문구 후보를 뽑아 만든 클러스터 인덱스.

    실제 빌드/캐시 로직은 `_phrase_bundle()`에 있다(사후 이벤트 라벨과 원자적으로
    묶어야 하는 이유는 그쪽 docstring 참고) — 이 함수는 클러스터 인덱스만 필요한
    기존 호출부(`build_report()` 등)를 위한 얇은 래퍼다.
    """
    return _phrase_bundle()[0]


@st.cache_resource(show_spinner=False)
def _outcome_labels() -> None:
    """`_phrase_bundle()`이 만든 사후 이벤트 라벨을 전역 상태에 등록한다.

    라벨을 만드는 로직 자체는 `_phrase_bundle()`로 옮겨갔다(클러스터 인덱스와 원자적
    으로 묶여야 하는 이유는 그쪽 docstring 참고) — 이 함수는 그 결과를
    `register_outcome_labels()`에 넘기는 부작용만 담당한다. 반환값이 없는 이유
    (`-> None`)는 이 함수의 목적이 값을 만드는 게 아니라 그 전역 부작용을 앱 부팅 시
    딱 한 번만 일으키는 것이기 때문이다(`@st.cache_resource`가 재실행을 막아준다).
    """
    _, labels = _phrase_bundle()
    register_outcome_labels(labels)
    return None


def _build_card(
    text: str, *, corp_code: str | None = None, force_corp_sample: bool = False
) -> tuple[ReportCard, bool]:
    """(카드, 샘플 여부)를 반환한다.

    실제 결합 로직은 report/card.py::build_report(L3)에 있다 — B의 엔진
    (run_factcheck/run_dictionary_lookup)이 아직 미구현이면 NotImplementedError가
    올라오는데, 여기서 잡아 레이아웃 확인용 샘플 카드로 대체한다.

    샘플도 **실제로 시도됐을 경로에 맞춰** 고른다(PR #33 리뷰 지적, @powercodingman) —
    종목이 특정되지 않은 입력인데 팩트체크 카드를 같이 그려버리면, 엔진이 붙은 뒤엔
    절대 나오지 않을 화면을 팀원들에게 보여주게 된다.

    `force_corp_sample`은 그 반대 방향의 한계를 푸는 데모 전용 스위치다(PR #33 추가
    리뷰, @powercodingman) — `extract_claim`(B 담당)이 스텁인 동안 `resolve_claim`은
    항상 종목 미특정을 돌려주므로, 놔두면 "종목이 특정됐을 때" 화면을 자체적으로 볼
    방법이 없다. 종목명 추측 휴리스틱을 `resolve_claim`에 넣는 대신 여기서 명시적으로
    끄고 켠다 — 추측 로직이 L3(report/card.py)에 들어가면 B의 `extract_claim` 자리를
    미리 점유해버리고, 데모에서만 맞는 동작이 프로덕션 경로에 남는다.

    `corp_code`(2026-09-18 추가): `main()`의 종목 선택 드롭다운이 넘기는 값 —
    `build_report(corp_code_override=...)`로 그대로 전달해 회사명 추출/매칭에 의존하지
    않고 바로 그 회사로 조회한다.
    """
    try:
        # compute_stats()가 read()하는 register_outcome_labels() 전역 상태를, 그 상태를
        # 만드는 phrase_index 와 같은 부팅 과정 안에서 먼저 채워둔다(모듈 docstring
        # "사후 이벤트 라벨 배선" 참고). @st.cache_resource 라 재호출해도 한 번만 실행된다.
        _outcome_labels()
        card = build_report(
            text,
            company_index=_company_index(),
            corp_code_index=_corp_code_index(),
            phrase_index=_phrase_index(),
            corp_code_override=corp_code,
        )
        return card, False
    except NotImplementedError:
        claim = resolve_claim(text)
        if corp_code:
            claim.corp_code = corp_code
        if not (claim.corp_code or claim.corp_name) and not force_corp_sample:
            # 문구 단독 검색 경로 — 종목이 없으면 붙일 팩트체크/맥락 지도가 없다.
            return ReportCard(fact_check=None, phrase_stats=None), True
        sample_fact_check = FactCheckResult(claim=text, items=_SAMPLE_FACT_CHECK.items)
        return ReportCard(fact_check=sample_fact_check, phrase_stats=None), True


def _dart_source_url(rcept_no: str) -> str:
    """DART 원문 조회 페이지 링크. gold set(eval/gold/rumor_cases.jsonl)의
    source_url과 같은 형식 — rcept_no만 있으면 구성 가능해서 별도 저장 없이 그
    자리에서 만든다."""
    return f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"


def _format_rcept_dt(rcept_dt: str) -> str | None:
    """DART의 YYYYMMDD 문자열을 "YYYY-MM-DD"로. 값이 없거나 형식이 안 맞으면 None —
    호출부가 조용히 생략하게 한다(날짜 표시가 필수 정보는 아니라서)."""
    if len(rcept_dt) != 8 or not rcept_dt.isdigit():
        return None
    return f"{rcept_dt[:4]}-{rcept_dt[4:6]}-{rcept_dt[6:]}"


def _render_fact_line(item: FactCheckItem, *, source_number: int | None = None) -> None:
    """fact 한 줄 + 공시 유형 배지 + 날짜 + 확정 여부 + 재공시 예정을 함께 그린다.

    "몇 개월 전에 나온 미확정 해명"과 "방금 확정된 결정"은 사용자가 판단을 내릴 때
    무게가 완전히 다른데, fact 문장만으로는 이 구분이 안 드러난다 — 팩트체크 결과의
    실질적 유용성을 위해 fact 옆에 이 맥락 정보를 그대로 붙인다(투자 판단 자체는
    여전히 내리지 않는다 — 사실과 시점만 추가로 보여줄 뿐).
    """
    st.markdown(f"- {item.fact}")
    section = item.supporting_section
    badges = []
    if section is not None:
        if source_number is not None:
            badges.append(f"[근거 {source_number}](#source-{source_number})")
        badges.append(f"`{section.form_type}`")
        formatted_date = _format_rcept_dt(section.rcept_dt)
        if formatted_date:
            badges.append(formatted_date)
    # 확정된 사실이 기본값(대다수)이라 매번 "확정"이라고 찍으면 노이즈만 늘어난다 —
    # 예외적인 상태(미확정·검토중)일 때만 배지를 보여준다.
    if not item.is_confirmed:
        badges.append("🕒 미확정·검토중")
    if badges:
        st.caption(" · ".join(badges))
    if item.next_update_note:
        st.caption(f"📅 **다음 공시 예정** — {item.next_update_note}")


def _render_fact_check(result: FactCheckResult) -> None:
    st.header("공시 사실 대조")
    st.caption(
        "문장의 진위를 판정하지 않습니다. 관련 공시에서 뒷받침하는 사실과 배치되는 사실을 "
        "같은 비중으로 보여드립니다."
    )
    if result.status == "error":
        if result.reason == "unresolved_company":
            st.warning("회사를 확인하지 못했습니다. 종목을 선택한 뒤 다시 조회해 주세요.")
        else:
            st.error("공시 분석을 완료하지 못했습니다. 잠시 후 다시 시도해 주세요.")
        return
    if not result.items:
        reasons = {
            "low_relevance": "검색된 공시의 관련성이 부족하여 사실 추출을 생략했습니다.",
            "no_facts": "검색된 공시에서 이 사안과 관련된 사실을 확인하지 못했습니다.",
            "no_grounded_facts": "검색된 공시에서 근거가 확인된 사실을 찾지 못했습니다.",
            "filtered_facts": "검증을 통과하여 표시할 수 있는 사실을 찾지 못했습니다.",
        }
        st.info(
            reasons.get(result.reason, "관련 공시를 찾지 못했습니다.")
            + " 이 코퍼스 범위 안에서는 확인도 반박도 할 수 없습니다."
        )
        return

    reviewed_rcept_nos = {
        item.supporting_section.rcept_no for item in result.items if item.supporting_section
    }
    if reviewed_rcept_nos:
        st.caption(f"근거로 채택된 공시 {len(reviewed_rcept_nos)}건")

    # 근거의 '시점'을 사실 그대로 노출한다(2026-09-30). 평가 표현("오래된/최신")은 쓰지
    # 않는다 — 그 자체가 "무시해도 된다"는 판단으로 읽히기 때문이다(CLAUDE.md 주의점 1).
    evidence_dates = sorted(
        item.supporting_section.rcept_dt
        for item in result.items
        if item.supporting_section and item.supporting_section.rcept_dt
    )
    if evidence_dates:
        latest = _format_rcept_dt(evidence_dates[-1])
        collected_through = _corpus_collected_through()
        if latest and collected_through:
            st.caption(
                f"🗓️ 가장 최근 근거 {latest} · 이 코퍼스는 {collected_through}까지 수집됐습니다"
                " — 그 이후 공시는 포함되지 않습니다."
            )
        elif latest:
            st.caption(f"🗓️ 가장 최근 근거 {latest}")

    scheduled = sum(1 for item in result.items if item.next_update_note)
    if scheduled:
        st.caption(
            f"📅 재공시 예정이 명시된 사실 {scheduled}건 — 각 사실 아래에 일정을 적었습니다."
        )

    # 같은 문서의 다른 절은 보존한다. 섹션의 모든 필드가 같은 경우에만 한 번 표시한다.
    sources: list[DisclosureSection] = []
    for item in result.items:
        if item.supporting_section is not None and item.supporting_section not in sources:
            sources.append(item.supporting_section)

    def render_item(item: FactCheckItem) -> None:
        number = sources.index(item.supporting_section) + 1 if item.supporting_section else None
        _render_fact_line(item, source_number=number)

    def _recency_key(item: FactCheckItem) -> str:
        """접수일 내림차순 정렬용 키. 날짜가 없는 근거는 맨 뒤로 보낸다."""
        section = item.supporting_section
        return section.rcept_dt if section and section.rcept_dt else ""

    by_polarity: dict[str, list[FactCheckItem]] = {"positive": [], "negative": [], "neutral": []}
    unexpected: list[FactCheckItem] = []
    for item in result.items:
        if item.polarity in by_polarity:
            by_polarity[item.polarity].append(item)
        else:
            # FactCheckItem.polarity 는 str이라 verify_claim()이 "positive"/"negative"/
            # "neutral" 외의 값(오탈자·새 카테고리)을 리턴해도 강제되지 않는다. 예전엔
            # setdefault로 새 버킷만 만들고 어느 컬럼에도 안 그려서 조용히 사라졌다
            # (PR #33 리뷰 지적, @powercodingman) — CLAUDE.md 주의점 2("부정/긍정 동일
            # 비중")가 지키려는 지점이 바로 이거라, 안 버리고 아래에 명시적으로 그린다.
            unexpected.append(item)

    # 긍정/부정 동일 비중 노출(CLAUDE.md 주의점 2) — 2단 컬럼으로 나란히 배치해
    # 어느 한쪽에도 시각적 우위를 주지 않는다.
    for bucket in by_polarity.values():
        bucket.sort(key=_recency_key, reverse=True)

    col_pos, col_neg = st.columns(2)
    with col_pos:
        st.subheader("뒷받침하는 사실", anchor=False)
        for item in by_polarity["positive"]:
            render_item(item)
        if not by_polarity["positive"]:
            st.caption("찾아봤지만 없음")
    with col_neg:
        st.subheader("배치되는 사실", anchor=False)
        for item in by_polarity["negative"]:
            render_item(item)
        if not by_polarity["negative"]:
            st.caption("찾아봤지만 없음")

    if by_polarity["neutral"]:
        st.subheader("참고 사실", anchor=False)
        st.caption("같은 주제지만, 입력한 문장을 뒷받침하거나 반박하지 않는 내용입니다.")
        for item in by_polarity["neutral"]:
            render_item(item)

    if unexpected:
        # 사용자에게는 "분류되지 않았지만 버리지 않았다"는 사실만 보이면 된다 — 예전
        # 문구("polarity 값이 ... verify_claim() 확인 필요")는 내부 함수명이 그대로
        # 노출되는 개발자용이었다. 진단에 필요한 원래 값은 로그로 돌린다(화면 대신).
        unknown_values = sorted({item.polarity for item in unexpected})
        _logger.warning(
            "FactCheckItem.polarity 예상 범위 밖 %d건: %s — verify_claim() 확인 필요",
            len(unexpected),
            unknown_values,
        )
        st.subheader("분류되지 않은 사실", anchor=False)
        st.caption("긍정/부정 어느 쪽으로도 분류되지 않았지만, 빠뜨리지 않기 위해 함께 표시합니다.")
        for item in unexpected:
            render_item(item)

    if sources:
        with st.container(key="evidence-sources"):
            st.subheader("근거 원문", anchor=False)
            for number, section in enumerate(sources, start=1):
                st.html(f'<div id="source-{number}" class="source-anchor" tabindex="-1"></div>')
                label = f"근거 {number} · {section.form_type} · {section.section_title}"
                date = _format_rcept_dt(section.rcept_dt)
                if date:
                    label += f" · {date}"
                with st.expander(label):
                    st.write(section.text)
                    st.markdown(f"[DART 원문 보기]({_dart_source_url(section.rcept_no)})")


@st.cache_resource(show_spinner=False)
def _corpus_collected_through() -> str | None:
    """코퍼스에 담긴 가장 최근 공시의 접수일("YYYY-MM-DD"). 비어 있으면 None.

    근거 날짜만 보여주면 **"그 뒤로 아무 일도 없었다"는 착시**가 생긴다 — 배치 수집이라
    수집이 거기서 끝났을 뿐일 수 있다. 그래서 근거의 최신 날짜 옆에 이 값을 같이 적는다.

    **캐싱하는 이유**(2026-09-30 리뷰 지적, @9abin): `_local_sections()` 자체는 캐싱돼
    있어 재로드는 없지만, 순회와 `max()`는 rerun마다 반복된다 — 위젯을 건드릴 때마다
    최대 8만여 섹션을 다시 훑는 낭비다. 코퍼스가 바뀌지 않는 한 결과도 같으므로 바로
    아래 `_corpus_scope_summary()`와 같은 방식으로 캐싱한다. 테스트에서 코퍼스를
    바꿔 끼울 땐 `st.cache_resource.clear()`로 함께 비워야 한다.
    """
    sections = _local_sections()
    dates = [s.rcept_dt for s in sections if s.rcept_dt]
    return _format_rcept_dt(max(dates)) if dates else None


@st.cache_resource(show_spinner=False)
def _corpus_scope_summary() -> str:
    """화법통계 카드에 붙일 코퍼스 범위 투명성 문구(회사 수·공시 시기).

    회사 단위 중복 제거(fix/cluster-stats-company-dedup, PR #64)로 pseudoreplication은
    바로잡았지만, `compute_stats()`가 참조하는 코퍼스 자체가 여전히 특정 회사군·특정
    시기(현재 로컬 수집분)로 한정돼 있다는 사실은 그 수정과 별개다 — 표본이 이 범위
    밖(다른 시기·다른 업종 비중)에도 같은 비율로 일반화된다는 보장은 없다. 이 범위를
    통계 옆에 그대로 노출해 "과거 N건 중 M건"이 이 코퍼스 한정 관측치임을 명시한다
    (held-out 분할로 일반화 자체를 검증하는 문제는 별도 — Phase 4 고려사항으로 이슈에
    남겨둠).
    """
    sections = _local_sections()
    if not sections:
        return ""
    corp_codes = {s.corp_code for s in sections}
    dates = sorted(s.rcept_dt for s in sections if s.rcept_dt)
    if dates:
        period = f"{dates[0][:4]}-{dates[0][4:6]}~{dates[-1][:4]}-{dates[-1][4:6]}"
    else:
        period = "시기 미상"
    return f"{len(corp_codes)}개사, {period} 공시 기준"


@st.cache_resource(show_spinner="표현들이 어떤 공시 유형에 속하는지 정리하고 있습니다...")
def _cluster_meanings() -> dict[str, ClusterMeaning]:
    """클러스터별 "의미" — 부팅 시 한 번 계산한다.

    2026-09-18 재구성: "사후이벤트"를 미래에 일어날 다른 사건이 아니라, **그 표현이
    원래 속한 공시 유형(form_type) 자체**로 재정의한다 — 상투구 클러스터가 "미래
    사후 이벤트"를 예측한다는 원래 가설은 held-out 검증에서 반증됐지만(재현율 0%,
    클러스터 세밀도를 조정해도 마찬가지 — dictionary/cluster_meaning.py 모듈
    docstring 참고), "이 클러스터가 어떤 form_type에서 주로 나타나는가"는 예측이
    아니라 분류라 안정적으로 성립한다(실측치·모집단 변경 이력은 cluster_meaning.py
    모듈 docstring 참고). 이게 이 기능이 다루는
    전부다 — 여기에 "그리고 그 유형이 미래에 다시 나타난다"를 덧붙이면 다시 예측
    주장이 된다(demo/app.py::_build_meaning_graph_dot 참고).

    새 임베딩 호출이 없다(이미 만들어진 `_phrase_bundle()`의 centroid와 비교만).

    **2026-09-25 디스크 캐싱 추가**: 예전 이 docstring은 "순수 문자열 스캔이라
    82,876섹션 기준 실측 수십 초라 무시할 수준이라 디스크 캐싱은 하지 않는다"고
    적고 있었는데, 응답시간 실측(`docs/testing/response-time.md`)에서 **서버 기동마다
    약 4분 20초**로 나왔다 — 캐시되는 클러스터 번들 생성(~134초)보다도 길다.
    `@st.cache_resource`는 프로세스 안에서만 살아남아 재시작하면 그 시간을 다시
    쓰므로, 결과를 코퍼스 지문별로 디스크에 남긴다(`cache_store`). 느려진 원인은
    2026-09-22에 문장 추출 정규식이 늘어난 영향으로 **추정**하며 프로파일링은 하지
    않았다 — 캐시는 그 원인과 무관하게 재기동 비용만 없앤다.
    """
    sections = _local_sections()
    if not sections:
        return {}
    index, _ = _phrase_bundle()
    clusters, _centroids = index.dump()
    if not clusters:
        return {}

    fingerprint = cache_store.cluster_meanings_fingerprint(sections, _RAW_DIR)
    cached = cache_store.load_cluster_meanings(_CACHE_DIR, fingerprint)
    if cached is not None:
        return cached

    candidate_phrases, _ = select_clustering_pool(sections)
    meanings = compute_cluster_meanings(index, candidate_phrases, sections)
    cache_store.save_cluster_meanings(_CACHE_DIR, fingerprint, meanings)
    return meanings


@st.cache_resource(show_spinner="과거 반복 패턴을 확인하고 있습니다...")
def _recurrence_signals() -> dict[str, RecurrenceSignal]:
    """form_type 자기상관 안정성 신호 — 부팅 시 한 번 계산한다(임베딩 불필요).

    2026-09-18 재도입: "이 유형은 과거에 반복해서 나타난 경향이 있습니다"라는,
    이미 지나간 관측을 서술하는 통계다(dictionary/recurrence.py 모듈 docstring
    참고) — "앞으로도 반복될 것"이라는 예측이 아니라 "지금까지 코퍼스에서 그래
    왔다"는 과거형 사실이라는 게 UI 문구에서 분명해야 한다(`_build_meaning_snapshot()`
    참고). 여러 무작위 분할(기본 30회)에 걸쳐 안정적으로 재현되는 조합만 통과한다 —
    단일 분할은 표본이 작아 seed에 따라 재현율이 크게(37~65%) 흔들린다는 게
    실측으로 확인됐다.
    """
    sections = _local_sections()
    if not sections:
        return {}
    post_events = load_post_event_labels(_RAW_DIR)
    labels = build_form_type_labels(sections, post_events)
    if not labels:
        return {}
    form_types = sorted({s.form_type for s in sections})
    return compute_recurrence_signals(labels, form_types)


@st.cache_resource(show_spinner=False)
def _corp_names() -> dict[str, str]:
    """corp_code -> corp_name — `corpus_companies.json`(A10 선정 결과)에서 읽는다.

    `_build_meaning_snapshot()`이 팩트체크 근거 원문의 corp_code만 갖고 있을 때(LLM을
    다시 안 부르고, 위 함수 docstring 참고) 사람이 읽을 이름으로 보여주는 용도, 그리고
    `main()`의 종목 선택 드롭다운(2026-09-18 추가) 목록 자체로도 쓰인다 — 없으면
    (파일이 없거나 그 회사가 목록에 없으면) corp_code를 그대로 보여준다.

    실제 파일 읽기는 `dictionary/candidates.py::load_corpus_companies()`에 있다 —
    이 함수 안에 직접 두지 않는 이유는 그 함수 docstring 참고(AppTest 재실행 특성상
    app.py에 직접 정의된 이름은 테스트에서 monkeypatch로 못 가로챔).
    """
    return load_corpus_companies(_RAW_DIR)


def _escape_dot_label(text: str, *, max_len: int = 60) -> str:
    cleaned = text.replace('"', "'").replace("\n", " ").replace("\\", "/")
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len] + "…"
    return cleaned


def _build_meaning_graph_dot(
    *,
    corp_name: str,
    representative_phrase: str,
    form_type: str,
    purity: float,
    trend_hit: CompanyTrendItem | None,
    recurrence: RecurrenceSignal | None,
) -> str:
    """회사 -> 표현 -> 의미(form_type) -> (있으면) 과거 반복 경향 흐름을 그래프로 그린다.

    2026-09-18: "사후이벤트"를 미래에 일어날 다른 사건이 아니라 **그 표현이 원래
    속한 공시 유형(form_type) 자체**로 재정의한다 — "이 표현을 쓰면 나중에 X가
    일어난다"가 아니라 "이 표현은 X 유형에 속하는 표현이다"라는 분류다. `recurrence`
    노드는 그 뒤에 붙는 참고 정보인데, **과거 시제로만** 쓴다("반복돼 왔다"O,
    "반복될 것이다"X) — "이 유형이 앞으로도 반복된다"는 미래 예측이 아니라 "지금까지
    코퍼스에서 관측된 과거 패턴"이라는 걸 문구 자체로 분명히 한다.

    `trend_hit`(회사 자신의 최근 흐름)도 "최근 3개월 vs 그 이전"처럼 이미 지나간 두
    구간을 비교하는 서술일 뿐 미래를 주장하지 않는다.

    색상은 노드 종류를 한눈에 구분하기 위한 것일 뿐 의미의 경중을 나타내지 않는다
    (2026-09-18 UI 개선 — 전부 같은 회색이라 어디가 어딘지 구분하기 어렵다는 지적).
    """
    lines = [
        "digraph G {",
        '  rankdir="LR";',
        '  bgcolor="transparent";',
        '  node [shape=box, style="rounded,filled", fontname="Malgun Gothic", fontsize=12, '
        'margin="0.15,0.1"];',
        '  edge [color="#9aa5b1"];',
        f'  company [label="🏢 {_escape_dot_label(corp_name)}", fillcolor="#e3f2fd"];',
        f'  phrase [label="💬 최근 표현\\n{_escape_dot_label(representative_phrase)}", '
        'fillcolor="#f0f2f6"];',
        f'  meaning [label="🏷️ {_escape_dot_label(form_type)}\\n(대표 표현 순도 {purity:.0%})", '
        'fillcolor="#e8f5e9"];',
        "  company -> phrase;",
        "  phrase -> meaning;",
    ]
    if recurrence is not None:
        # recur_label은 숫자 포맷팅 + 고정 문구로만 조립돼 인용부호·백슬래시가 섞일
        # 수 없다 — `\n`을 또 이스케이프해 깨뜨리지 않도록 `_escape_dot_label()`을
        # 통과시키지 않는다(동적 텍스트 조각에만 적용하는 게 원칙, 위 노드들처럼).
        #
        # 2026-09-22 규제 방어선 점검(F3): 예전 문구 "안정성 N%"는 금융 맥락에서 **재무
        # 안정성(건전성)**으로 읽혔다. 실제 값은 "학습 절반에서 유의했던 분할 중 다른
        # 절반에서도 재현된 비율"(dictionary/recurrence.py `stability`)이라 그 뜻을 그대로
        # 풀어 쓴다 — 회사를 평가하는 지표처럼 보이는 단어를 쓰지 않는다.
        recur_label = (
            f"🔁 과거에 반복돼 온 유형\\n(무작위 분할 {recurrence.n_seeds}회 중 "
            f"{recurrence.n_train_significant}회 관측,\\n"
            f"그중 {recurrence.stability:.0%}가 다른 절반에서도 재현)"
        )
        lines.append(f'  recurrence [label="{recur_label}", fillcolor="#f3e5f5"];')
        lines.append("  meaning -> recurrence;")
    if trend_hit is not None:
        # 2026-09-22 규제 방어선 점검(F2): 예전 라벨 "📈 이 회사의 최근 흐름: 증가"는
        # 세 겹으로 주가 추세처럼 읽혔다 — ① 📈는 금융 서비스에서 곧 "주가 상승" 아이콘이고
        # "감소"일 때도 그대로 떴다, ② **무엇의** 흐름인지가 빠져 있었다(실제 값은 이 회사
        # 공시 중 해당 유형이 차지하는 비중), ③ 노드가 회사에 붙어 "회사의 흐름"이라는
        # 오독을 굳혔다. 방향성 없는 아이콘으로 바꾸고, 대상(유형 비중)을 라벨에 명시하고,
        # 유형(meaning) 노드에 붙인다 — 신호등·등급처럼 보이는 요소를 배제하는 방어선
        # (03-integrated-proposal.md 7절)과 같은 원칙이다.
        direction = "증가" if trend_hit.is_increasing else "감소"
        trend_label = (
            f"🗓️ 이 회사 공시 중 '{_escape_dot_label(trend_hit.form_type)}' 비중\\n"
            f"최근 {trend_hit.recent_rate:.0%} · 이전 {trend_hit.baseline_rate:.0%}"
            f" (비중 {direction})"
        )
        lines.append(f'  trend [label="{trend_label}", fillcolor="#fff3e0"];')
        lines.append("  meaning -> trend;")
    lines.append("}")
    return "\n".join(lines)


def _match_cluster_for_text(
    phrase_index: PhraseClusterIndex, text: str, meanings: dict[str, ClusterMeaning]
) -> PhraseCluster | None:
    """`text` 전체를 통째로 먼저 매칭해보고, 실패하면(혹은 신뢰 불가능하면) 그 안에서
    뽑은 서술형 후보 문장을 개별적으로 매칭해 신뢰 가능한 대안을 찾는다.

    실측(2026-09-18, 사용자 제보 — 삼성전자 공정거래법 제26조 대규모내부거래 공시)
    — 표 양식 공시는 항목 라벨·숫자 필드가 섞여 있어, 섹션 전체를 하나로 임베딩하면
    실제로 포함된 서술형 상투구가 있어도 유사도가 문턱(0.75)을 못 넘는다(실측
    최고점 0.47). `build_outcome_labels()`가 코퍼스 빌드 시점에 쓰는 "섹션→후보
    추출(`extract_phrase_candidates`)→일괄 매칭(`match_many`)" 패턴을 질의 시점에도
    재사용해, 표 안에 묻힌 서술형 문장이 있으면 그걸로 다시 시도한다.

    R104-1 (2026-09-21 외부 리뷰) 수정: 점 2 수정(회사 수 조건을 인덱스 생성이 아니라
    `ClusterMeaning.is_reliable`로 분리)으로 신뢰 불가능한 클러스터도 인덱스에 남게
    됐다. 그런데 예전처럼 "첫 매칭에서 멈추면", 같은 입력 안에 신뢰 가능한 대안이
    있어도 희귀 클러스터에 먼저 걸려 화면이 통째로 생략되는 문제가 생긴다(실코퍼스
    골드셋 재현: 원시 매칭 25/25 vs 화면 표시 8/25). 그래서 전체 텍스트 매칭이든
    후보 매칭이든, `meanings`에서 `is_reliable=True`로 확인되는 첫 클러스터를
    우선 반환한다. 전체 텍스트가 이미 신뢰 가능하면 그 자리에서 바로 반환해(불필요한
    후보 추출을 피한다) 예전의 단락 매칭 시 짧은 경로를 유지한다. 신뢰 가능한 후보가
    끝내 하나도 없으면, 표시 가능한 대안이 전혀 없다는 뜻이므로 예전처럼 가장 먼저
    매칭된 클러스터를 그대로 반환한다(호출부 `_build_meaning_snapshot()`이 이를 보고
    생략을 결정한다 — 회사 수 기준을 낮춰 표시율만 억지로 회복하는 방식이 아니다).
    """
    matched: list[PhraseCluster] = []
    whole = phrase_index.match(text)
    if whole is not None:
        matched.append(whole)
        whole_meaning = meanings.get(whole.cluster_id)
        if whole_meaning is not None and whole_meaning.is_reliable:
            return whole
    ad_hoc_section = DisclosureSection(
        rcept_no="", corp_code="", form_type="", section_title="", text=text
    )
    candidates = extract_phrase_candidates([ad_hoc_section])
    if candidates:
        for cluster in phrase_index.match_many(candidates):
            if cluster is None:
                continue
            matched.append(cluster)
            meaning = meanings.get(cluster.cluster_id)
            if meaning is not None and meaning.is_reliable:
                return cluster
    return matched[0] if matched else None


def _render_meaning_map_unavailable(reason: str) -> None:
    """맥락 지도를 그릴 수 없을 때 그 사실을 알린다.

    2026-09-22 UX 점검: 예전엔 매칭 실패·신뢰도 미달 시 **아무것도 그리지 않고
    조용히 빠져나갔다**(Issue #32의 "매칭 실패 시 섹션 생략" 관례). 그런데 이 화면은
    R104-1에서 "신뢰도 낮은 클러스터에 가로막혀 25건 중 8건만 노출"되던 전력이 있어,
    비어 있는 화면을 보고 **고장인지 해당 없음인지 사용자가 구분할 수 없다**는 게 실제
    문제였다. 한 줄이라도 이유를 보여주면 둘이 구분된다.

    "표본이 부족하면 유의하지 않음을 명시한다"(CLAUDE.md 주의점 3)와도 같은 방향이다 —
    없는 걸 없다고 말하는 것이지, 없는 걸 있는 척하지 않는다.
    """
    st.header("🔍 이 표현이 가리키는 맥락")
    st.info(f"{reason} 이 표현에 대한 맥락 정보는 표시하지 않습니다.")


def _build_meaning_snapshot(text: str, card: ReportCard) -> MeaningSnapshot:
    """2·3·4번 기능(흔함도 체커·회사별 흐름·과거 반복 경향)을 하나의 동적 그래프로
    보여준다 — 전부 서술적 사실만 다루고, "예측"은 하지 않는다(모듈 상단 설명,
    `_build_meaning_graph_dot()` docstring 참고).

    매칭 대상 텍스트는 팩트체크가 근거로 찾은 공시 원문이 있으면 그걸 쓰고(실제
    공시 문장 기준), 없으면(종목 미특정 경로) 입력 문장 자체를 쓴다 — 기존
    build_report()의 "화법 문구 단독 검색" 관례와 동일하다.
    """
    match_text = text
    corp_name = "(종목 미특정)"
    corp_code: str | None = None
    if card.fact_check is not None:
        for item in card.fact_check.items:
            if item.supporting_section is not None:
                match_text = item.supporting_section.text
                corp_code = item.supporting_section.corp_code
                corp_name = _corp_names().get(corp_code, corp_code)
                break
        # 근거 공시를 못 찾았으면(위 루프에서 corp_code를 못 얻음) 회사명을 다시
        # 해석하지 않는다 — `resolve_claim()`을 여기서 또 부르면 build_report()가
        # 이미 한 번 호출한 것과 별개로 LLM을 한 번 더 호출하게 된다(불필요한
        # 비용·지연 — 리뷰 지적, PR #90 @jh3779: card.fact_check.items=[] 케이스에서
        # for 루프가 아예 안 돌아도 이 호출은 무조건 실행돼, 실제 API 크리덴셜이
        # 없는 테스트 환경에서 openai.OpenAIError로 CI가 깨졌다). 그 경우 이 맥락
        # 지도는 "종목 미특정"과 동일하게 취급한다(입력 문장 자체로 문구 매칭만
        # 시도).

    phrase_index = _phrase_index()
    meanings = _cluster_meanings()
    cluster = _match_cluster_for_text(phrase_index, match_text, meanings)
    if cluster is None:
        return MeaningSnapshot(reason="이 문장과 비슷한 상투구를 코퍼스에서 찾지 못했습니다.")
    meaning = meanings.get(cluster.cluster_id)
    if meaning is None or not meaning.is_reliable:
        # `_match_cluster_for_text()`가 신뢰 가능한 대안을 이미 다 찾아봤는데도
        # 없었다는 뜻이다(R104-1) — 순도가 낮거나(여러 form_type에 흩어짐) 표본이
        # 부족한 경우다.
        return MeaningSnapshot(
            reason="비슷한 표현은 찾았지만, 여러 공시 유형에 흩어져 있거나 표본이 부족해"
            " 맥락을 단정할 수 없습니다."
        )

    trend_hit = None
    if corp_code:
        trend = compute_company_trend(corp_code, _local_sections())
        trend_hit = next((t for t in trend if t.form_type == meaning.dominant_form_type), None)

    recurrence = _recurrence_signals().get(meaning.dominant_form_type)

    return MeaningSnapshot(
        graph_dot=_build_meaning_graph_dot(
            corp_name=corp_name,
            representative_phrase=meaning.representative_phrase,
            form_type=meaning.dominant_form_type,
            purity=meaning.purity,
            trend_hit=trend_hit,
            recurrence=recurrence,
        ),
        scope=_corpus_scope_summary(),
    )


def _render_meaning_snapshot(snapshot: MeaningSnapshot) -> None:
    """조회 당시의 맥락만 그린다. 기록 열람으로 매칭·통계를 다시 계산하지 않는다."""
    if snapshot.reason:
        _render_meaning_map_unavailable(snapshot.reason)
        return
    st.header("🔍 이 표현이 가리키는 맥락")
    st.graphviz_chart(snapshot.graph_dot)
    st.caption(
        "※ 예측이 아닙니다 — '이 표현이 흔히 나타나는 공시 유형', '이 유형이 과거 "
        "코퍼스에서 반복돼 온 정도', '이 회사 자신의 최근 공시 흐름'만 사실대로 "
        "보여줍니다. 앞으로 일어날 일을 예측하지 않습니다."
    )
    if snapshot.scope:
        st.caption(
            f"※ 이 통계는 조회 당시 수집된 코퍼스({snapshot.scope})에서 관측된 값입니다. "
            "이 범위 밖의 회사·시기에도 같은 비율로 일반화된다는 보장은 없습니다."
        )


def _render_glossary_tab() -> None:
    """공시 용어 사전 탭 — dictionary/glossary.py::GLOSSARY를 그대로 나열한다.

    이슈 #95(3안-A) 1차 구현 범위: 원문 안에 각주를 다는 인라인 방식이 아니라, 등록된
    용어·정의 전체를 사전처럼 훑어볼 수 있는 별도 탭. L2b 화법 클러스터(임베딩 매칭)와
    무관한 정적 딕셔너리 렌더링이라, 콜드스타트에 영향이 없다.
    """
    st.caption(
        f"공시에 자주 등장하는 용어 {len(GLOSSARY)}개를 정리했습니다. "
        "정부기관·법령 등 공신력 있는 출처를 참고해 중립적으로 서술했습니다."
    )
    # 2026-09-22 UX 점검: 20개를 평문으로 죽 나열하면 찾는 용어까지 스크롤해야 한다.
    # 용어명뿐 아니라 정의까지 훑어야 원하는 걸 찾는 경우가 많아(예: "희석"으로 찾으면
    # 전환사채 설명이 걸려야 한다) 양쪽 모두에서 찾는다.
    query = st.text_input(
        "용어 검색",
        placeholder="예: 전환사채, 희석",
        key="_glossary_query",
        help="용어명과 설명 양쪽에서 찾습니다.",
    ).strip()
    entries = list(GLOSSARY.values())
    if query:
        needle = query.casefold()
        entries = [
            e for e in entries if needle in e.term.casefold() or needle in e.definition.casefold()
        ]
        if not entries:
            st.info(f"'{query}'와 일치하는 용어가 없습니다. 다른 검색어를 시도해보세요.")
            return
        st.caption(f"{len(entries)}개 일치")
    for entry in entries:
        st.markdown(f"**{entry.term}** — {entry.definition}")
        st.caption(f"출처: {entry.source}")


# 2026-09-18 사용성 개선(사용자 요청 — "정확한 질문을 이끌어낼 요소가 부족") — 클릭 한 번으로
# 입력창을 채우는 예시 문구. "예: ..." 플레이스홀더 하나만으로는 어떤 형태의 문장이 좋은
# 결과를 내는지 감이 안 온다는 지적이라, 실제로 팩트체크·화법통계 양쪽 다 잘 걸리는 유형
# (사실관계 확인형/화법 확인형/신사업 루머형)을 각각 하나씩 고른다.
_EXAMPLE_QUERIES = (
    "유상증자를 검토 중이라는 루머가 있습니다.",
    "최근 전환사채를 발행했다고 들었습니다.",
    "신규 사업 진출설이 도는데 사실인가요?",
)

# 너무 짧은 입력은 팩트체크 대조·상투구 매칭 둘 다 근거가 부족해진다 — 실측으로 정한
# 값은 아니고, "예: 유상증자"처럼 명사 하나만 던지는 최소 입력을 막는 보수적인 하한선.
_MIN_QUERY_LENGTH = 8


@st.cache_data(show_spinner=False)
def _company_query_hint(corp_code: str) -> str | None:
    """종목 선택 직후 바로 보여줄 컨텍스트 힌트 — 사용자 요청("종목 선택 직후 컨텍스트
    요약")에 따라 추가했다.

    이 회사의 공시가 몇 건이고 최근엔 주로 어떤 유형이 올라왔는지 미리 보여줘서, "이
    회사에 대해 뭘 물어봐야 할지" 감을 잡게 돕는다. 선택한 회사의 파일만 읽고 작은 안내
    문자열만 캐시한다. 전체 코퍼스를 읽으면 첫 종목 선택만으로 불필요한 로딩이 생긴다.
    임베딩·네트워크 호출은 없으며 수집되지 않은 회사는 None으로 조용히 생략한다.
    """
    sections = [
        s
        for s in load_sections_jsonl(_RAW_DIR / corp_code / "sections.jsonl")
        if s.corp_code == corp_code
    ]
    if not sections:
        return None
    form_type_counts = Counter(s.form_type for s in sections)
    top_types = ", ".join(f"{ft}({count}건)" for ft, count in form_type_counts.most_common(3))
    dates = sorted(s.rcept_dt for s in sections if s.rcept_dt)
    if dates:
        period = f"{dates[0][:4]}-{dates[0][4:6]}~{dates[-1][:4]}-{dates[-1][4:6]}"
        period_part = f", {period}"
    else:
        period_part = ""
    return (
        f"📁 이 회사의 공시 {len(sections)}건을 보유하고 있습니다{period_part}. "
        f"자주 등장한 유형: {top_types}"
    )


# 조회 결과를 담아두는 세션 키. 값은 `LookupResult` 또는 오류 문자열.
_RESULT_KEY = "_last_result"
_ERROR_KEY = "_last_error"
_DRAFT_KEY = "_query_draft"
_PENDING_KEY = "_pending_lookup"
_LAST_REQUEST_KEY = "_last_lookup_request"
_PRINT_ONCE_KEY = "_print_result_once"


@dataclass(frozen=True)
class _LookupRequest:
    text: str
    corp_code: str
    corp_name: str
    force_corp_sample: bool


@dataclass(frozen=True)
class _QueryDraft:
    """페이지 이동 시 정리되는 위젯 값과 별도로 입력 초안을 보관한다."""

    corp_code: str | None = None
    corp_name: str | None = None
    text: str = ""
    example: str | None = None


def _run_lookup(text: str, *, corp_code: str, corp_name: str, force_corp_sample: bool) -> None:
    """조회를 수행하고 결과(또는 오류)를 세션 상태에 남긴다.

    **세션 상태에 남기는 이유**(2026-09-22 UX 점검): 예전엔 `if not st.button(...):
    return` 아래에서 곧바로 렌더링했는데, Streamlit 버튼은 **클릭한 그 재실행에서만**
    True다. 그래서 결과를 띄워둔 뒤 예시 칩을 고르거나 종목을 바꾸는 등 **어떤 위젯이든
    건드리는 순간 결과 전체가 사라졌다**(AppTest로 재현 확인). 시연 중에 화면이 비면
    재조회에 또 시간이 걸리므로 실사용·발표 양쪽에서 치명적이다.

    **예외를 잡는 이유**: `_build_card()`는 `NotImplementedError`만 잡는다. LLM
    타임아웃(PR #109에서 90초 상한을 걸었다)·레이트리밋·키 미설정은 그대로 올라와
    화면에 파이썬 스택트레이스가 그려졌다(역시 재현 확인). 사용자가 읽고 뭘 할지 알 수
    있는 문구로 바꾸되, 진단용 원본은 로그로 남긴다 — 조용히 삼키면 디버깅이 막힌다.
    """
    st.session_state.pop(_RESULT_KEY, None)
    st.session_state.pop(_ERROR_KEY, None)
    st.session_state.pop(_PRINT_ONCE_KEY, None)
    st.session_state[_LAST_REQUEST_KEY] = _LookupRequest(
        text=text, corp_code=corp_code, corp_name=corp_name, force_corp_sample=force_corp_sample
    )
    try:
        # show_time=True — 경과 시간을 같이 보여준다. 콜드스타트가 몇 분씩 걸리는데
        # 진행 표시가 스피너 하나뿐이면 멈춘 건지 도는 건지 구분이 안 된다(2026-09-22
        # UX 점검). 실제 소요 시간 측정은 별도 과제로 남아 있다.
        with st.spinner("공시를 대조하고 있습니다", show_time=True):
            card, is_sample = _build_card(
                text, corp_code=corp_code, force_corp_sample=force_corp_sample
            )
            meaning = None
            if not is_sample:
                try:
                    meaning = _build_meaning_snapshot(text, card)
                except Exception:  # noqa: BLE001 — 사실 대조 결과는 보존한다
                    _logger.exception("맥락 정보 준비 실패 — corp_code=%s", corp_code)
                    meaning = MeaningSnapshot(reason="조회 당시 맥락 정보를 불러오지 못했습니다.")
    except Exception as exc:  # noqa: BLE001 — 사용자에게 보일 화면을 지키는 게 우선
        _logger.exception("조회 실패 — corp_code=%s", corp_code)
        st.session_state[_ERROR_KEY] = f"{type(exc).__name__}: {exc}"
        return
    result = LookupResult(
        text=text,
        corp_code=corp_code,
        corp_name=corp_name,
        card=card,
        is_sample=is_sample,
        meaning=meaning,
    )
    get_history().add(result)
    st.session_state[_RESULT_KEY] = result
    st.session_state[_PRINT_ONCE_KEY] = True


def _render_query_context(corp_name: str, text: str) -> None:
    """긴 사용자 입력도 해석하지 않고 원문 그대로, 본문 크기로 보여준다."""
    with st.container(key="query-context"):
        st.caption(f"조회 기준 · {corp_name}")
        st.text(text)


def _render_last_result(*, current_corp_code: str | None, current_text: str) -> None:
    """세션에 남아 있는 마지막 조회 결과(또는 오류)를 그린다.

    결과 위에 조회 당시 종목·문장을 보여주고, 지금 화면의 입력과 다르면 "이전 조회
    결과"임을 알린다 — 결과를 보존하는 이점은 살리되 다른 입력의 결과로 오해하지
    않게 하기 위해서다(PR #113 리뷰).
    """
    error = st.session_state.get(_ERROR_KEY)
    if error:
        request = st.session_state.get(_LAST_REQUEST_KEY)
        if request:
            _render_query_context(request.corp_name, request.text)
        st.error(
            "조회 중 오류가 발생해 결과를 가져오지 못했습니다. 입력은 보관되어 있습니다. "
            "‘다시 조회하기’로 돌아가 잠시 후 다시 시도해주세요."
        )
        with st.expander("기술적 상세(팀 공유용)"):
            st.code(error)
            st.caption("계속 실패하면 운영자가 네트워크 연결과 LLM API 설정을 확인해야 합니다.")
        return

    result = st.session_state.get(_RESULT_KEY)
    if result is None:
        return

    _render_query_context(result.corp_name, result.text)
    if result.corp_code != current_corp_code or result.text != current_text.strip():
        st.info(
            "아래는 **이전 조회 결과**입니다 — 수정 중인 종목이나 문장과 다릅니다. "
            "새로 대조하려면 **다시 조회하기**로 돌아가 **확인**을 눌러주세요."
        )

    _render_saved_content(result)


def _render_saved_content(result: LookupResult) -> None:
    """입력 초안과 무관하게 보관된 사실·출처·맥락을 보여준다."""
    if result.is_sample:
        st.caption("🧪 팩트체크 엔진을 사용할 수 없어 레이아웃 확인용 샘플로 표시 중입니다.")
    if result.card.fact_check is not None:
        _render_fact_check(result.card.fact_check)
    if result.meaning is not None:
        _render_meaning_snapshot(result.meaning)


def _history_label(entry: HistoryEntry) -> str:
    # 라디오의 Markdown 문법이 사용자 입력을 링크/강조로 바꾸지 않도록 이스케이프한다.
    def plain(text: str) -> str:
        return "".join("\\" + c if c in "\\`*_{}[]<>()#+-.!|" else c for c in text)

    result = entry.result
    snippet = " ".join(result.text.split())
    if len(snippet) > 70:
        snippet = snippet[:69] + "…"
    stamp = entry.created_at.astimezone(ZoneInfo("Asia/Seoul")).strftime("%m.%d %H:%M")
    sample = " · 샘플" if result.is_sample else ""
    return (
        f"**{plain(result.corp_name)}**{sample}  \n{plain(snippet)}  \n"
        f"{stamp} · 서류 {entry.record_id:03d}"
    )


def _select_input_tab() -> None:
    st.session_state["_workspace_tab"] = "팩트체크·화법통계"


def _render_history_tab() -> None:
    entries = get_history().entries()
    with st.container(key="history-workspace"):
        st.header("조회 기록", anchor=False)
        st.caption(
            f"이 앱에서 조회한 최근 {HISTORY_LIMIT}건을 보관합니다. 새로고침·새 탭에서도 "
            "열 수 있으며, 서버를 재시작하거나 캐시를 지우면 초기화됩니다."
        )
        if not entries:
            st.info("아직 보관된 조회 결과가 없습니다. 조회를 완료하면 여기에 쌓입니다.")
            st.button("문장 조회하기", on_click=_select_input_tab)
            return
        documents, detail = st.columns([0.28, 0.72], gap="large")
        by_id = {entry.record_id: entry for entry in entries}
        if st.session_state.get("_history_selection") not in by_id:
            st.session_state["_history_selection"] = entries[0].record_id
        with documents:
            with st.container(key="history-list"):
                st.caption(f"보관된 서류 · {len(entries)}건 / 최신순")
                selected = st.radio(
                    "열람할 서류",
                    options=list(by_id),
                    key="_history_selection",
                    format_func=lambda record_id: _history_label(by_id[record_id]),
                    label_visibility="collapsed",
                )
        with detail:
            entry = by_id[selected]
            stamp = entry.created_at.astimezone(ZoneInfo("Asia/Seoul")).strftime(
                "%Y.%m.%d %H:%M:%S"
            )
            st.caption(f"서류 {entry.record_id:03d} · {stamp} 조회 · 조회 당시 결과")
            with st.container(key="printout"):
                _render_query_context(entry.result.corp_name, entry.result.text)
                _render_saved_content(entry.result)


def _apply_example_pick() -> None:
    """예시 칩 선택이 바뀐 순간 그 문장을 입력창에 채운다(선택 해제면 그대로 둔다)."""
    picked = st.session_state.get("_example_pick")
    if picked is not None:
        st.session_state["_query_text"] = picked


def _render_query_inputs(*, force_corp_sample: bool) -> tuple[str | None, str]:
    """기존 조회 입력을 그린다. 결과는 단말기 밖에서 렌더링한다."""
    # 2026-09-18 종목 선택 드롭다운 추가 — 이 서비스는 항상 회사 하나를 특정해
    # 조회하는 구조인데(사용자 요청), 예전엔 그 회사를 루머 문장에서 LLM이
    # 추출(`extract_claim`)한 뒤 `CorpCodeIndex.find_by_name()`으로 exact-match
    # 조회했다 — 법인격 표시("주식회사" 포함 여부) 같은 사소한 문구 차이로도
    # 실행마다 조회가 되다 안 되다 했다(PR #88). 사용자가 목록에서 직접 골라 그
    # corp_code를 `build_report(corp_code_override=...)`로 바로 넘기면 이 문제
    # 자체가 사라진다 — LLM은 이제 검색 질의문(keyword) 추출에만 쓰인다.
    #
    # 목록은 로컬 코퍼스(`corpus_companies.json`, 114개사)로 좁힌다 — DART 전체
    # 상장사(11만 건)까지 검색하게 하면, 실제 데이터가 없는 회사를 고르고 "공시를
    # 못 찾았다"는 결과만 받게 돼 오히려 혼란스럽다.
    corp_names = _corp_names()
    name_to_code = {name: code for code, name in corp_names.items()}
    if not name_to_code:
        st.warning(
            "조회 가능한 종목 목록을 불러오지 못했습니다 — 로컬 코퍼스가 아직 "
            "없거나 corpus_companies.json을 찾을 수 없습니다."
        )
        return None, ""

    draft = st.session_state.get(_DRAFT_KEY, _QueryDraft())
    st.session_state.setdefault(
        "_selected_corp", draft.corp_name if draft.corp_name in name_to_code else None
    )
    st.session_state.setdefault("_query_text", draft.text)
    st.session_state.setdefault("_example_pick", draft.example)
    corp_name = st.selectbox(
        "종목 선택",
        options=sorted(name_to_code),
        index=None,
        placeholder="조회할 종목을 선택하세요",
        key="_selected_corp",
    )
    # 2026-09-18 사용성 개선(사용자 요청 — "종목 선택 직후 컨텍스트 요약") — 회사를
    # 고르자마자 그 회사에 뭘 물어볼 수 있는지 힌트를 준다. 폼 제출 전이라 아직
    # corp_code가 필요한 다른 흐름(_build_card 등)에는 영향이 없다.
    if corp_name is not None:
        hint = _company_query_hint(name_to_code[corp_name])
        if hint:
            st.caption(hint)

    # 2026-09-18 사용성 개선(사용자 요청 — "예시 칩") — 고르면 바로 아래 입력창에
    # 채워진다. `key="_query_text"`로 text_area와 세션 상태를 공유하므로, 여기서
    # 세션 상태를 먼저 갱신한 뒤(이 시점에서 text_area는 아직 인스턴스화 전이라
    # 안전) 같은 재실행 안에서 text_area가 그 값을 그대로 집어간다.
    #
    # 2026-09-22: `st.columns(3)` + 버튼에서 `st.pills`로 바꿨다 — 고정 3분할은 좁은
    # 화면에서 긴 예시 문장이 뭉개지는데, pills는 폭에 맞춰 알아서 줄바꿈한다.
    #
    # 반영은 `on_change` 콜백에서만 한다 — 선택된 칩이 그대로 남아 있을 때 매
    # 재실행마다 덮어쓰면 사용자가 그 위에 직접 고친 내용이 날아간다. 예전엔
    # "마지막으로 적용한 칩"을 따로 기억해 비교했는데, 선택 해제 때 그 기록이 안
    # 지워져 같은 칩을 다시 골라도 반영되지 않았다(PR #113 리뷰, @9abin). 콜백은
    # 선택이 실제로 바뀐 순간에만 불리므로 별도 기록 자체가 필요 없다.
    st.pills(
        "예시를 눌러보세요",
        options=_EXAMPLE_QUERIES,
        selection_mode="single",
        key="_example_pick",
        on_change=_apply_example_pick,
    )

    text = st.text_area(
        "루머 또는 공시 문장",
        height=120,
        placeholder="예: 2차전지 신사업 MOU 체결설...",
        key="_query_text",
    )
    st.caption("어떤 사업·조치인지 구체적으로 적을수록 더 정확하게 대조할 수 있습니다.")
    # 2026-09-22 UX 점검: 종목 미선택이면 버튼 자체를 못 누르게 한다. 예전엔 누를
    # 수 있게 해놓고 누른 뒤에야 "종목을 선택해주세요"라고 알려줬다 — 왜 못 쓰는지를
    # 버튼 옆에서 미리 알려주는 쪽이 한 번 헛클릭하게 만드는 것보다 낫다.
    submitted = st.button(
        "확인",
        type="primary",
        disabled=corp_name is None,
        help=None if corp_name is not None else "먼저 종목을 선택해주세요.",
    )
    st.session_state[_DRAFT_KEY] = _QueryDraft(
        corp_code=name_to_code.get(corp_name),
        corp_name=corp_name,
        text=text,
        example=st.session_state.get("_example_pick"),
    )
    if submitted:
        stripped = text.strip()
        if not stripped:
            st.warning("문장을 입력해주세요.")
        elif len(stripped) < _MIN_QUERY_LENGTH:
            st.warning(
                f"문장이 너무 짧습니다({len(stripped)}자) — 어떤 사업·조치에 대한 "
                "이야기인지 조금 더 구체적으로 적어주시면 더 정확하게 대조할 수 "
                "있습니다."
            )
        else:
            st.session_state[_PENDING_KEY] = _LookupRequest(
                text=stripped,
                corp_code=name_to_code[corp_name],
                corp_name=corp_name,
                force_corp_sample=force_corp_sample,
            )
            st.switch_page(_result_page())

    return name_to_code.get(corp_name), text


def _render_input_page() -> None:
    powered = st.session_state.get("_monitor_on", False)
    force_corp_sample = st.session_state.get("_force_corp_sample", False)
    tab_check, tab_glossary, tab_history = st.tabs(
        ["팩트체크·화법통계", "사전", "조회 기록"], key="_workspace_tab", on_change="rerun"
    )
    with tab_history:
        if tab_history.open:
            _render_history_tab()

    with tab_glossary:
        with st.container(key="glossary"):
            _render_glossary_tab()

    with tab_check:
        with st.container(key="monitor"):
            with st.container(key="crt"):
                if powered:
                    st.html(
                        '<div class="terminal-boot"><strong>공시 대조 준비 완료</strong>'
                        "<br>종목을 선택하고, 확인할 문장을 입력하세요.</div>"
                    )
                    # 2026-09-18 사용성 개선(사용자 요청 — "결과 미리보기/온보딩 설명") — 처음 오는
                    # 사용자는 캡션 한 줄만으로는 이 서비스가 정확히 뭘 보여주는지 파악하기 어렵다.
                    # 기본 접힘 상태라 재방문자에게는 방해가 안 되면서도, 궁금하면 펼쳐볼 수 있다.
                    with st.expander("💡 무엇을 보여드리나요?", expanded=False):
                        st.markdown(
                            "- **공시 사실 대조**: 입력한 루머·문장을 실제 공시 원문과 대조해 "
                            "뒷받침하는 사실과 배치되는 사실을 나열합니다.\n"
                            "- **흔함도 체커**: 그 표현이 여러 회사가 흔히 쓰는 상투구인지, "
                            "어떤 공시 유형에 속하는지 보여줍니다.\n"
                            "- **회사별 흐름**: 선택한 회사가 최근 이 유형의 공시를 과거 대비 "
                            "늘렸는지 줄였는지 보여줍니다.\n"
                            "- **반복 경향**: 이 유형이 코퍼스 전체에서 과거에 얼마나 "
                            "반복돼 왔는지 보여줍니다.\n\n"
                            "모두 **사실 서술**일 뿐, 매수·매도·목표가 같은 "
                            "투자 판단은 하지 않습니다."
                        )
                    _render_query_inputs(force_corp_sample=force_corp_sample)
                else:
                    render_power_button()
            render_bezel(powered)

        if _RESULT_KEY in st.session_state or _ERROR_KEY in st.session_state:
            with st.container(key="last-result-nav"):
                if st.button("직전 결과 보기", key="last_result"):
                    st.switch_page(_result_page())


def _render_result_page() -> None:
    # 실행 전에 소비하여 페이지 재실행·재열람이 같은 요청을 중복 호출하지 않게 한다.
    pending = st.session_state.pop(_PENDING_KEY, None)
    with st.container(key="result-nav", horizontal=True):
        if st.button("다시 조회하기", key="back_to_input", disabled=pending is not None):
            st.switch_page(_input_page())
        if st.button("조회 기록", key="open_history", disabled=pending is not None):
            st.session_state["_workspace_tab"] = "조회 기록"
            st.switch_page(_input_page())
    with st.container(key="printer"):
        animate = st.session_state.pop(_PRINT_ONCE_KEY, False)
        render_printer_slot(animate=animate and pending is None)
        with st.container(key="printout"):
            _render_result_body(pending)


def _render_result_body(pending: _LookupRequest | None) -> None:
    if pending is not None:
        _render_query_context(pending.corp_name, pending.text)
        with st.container(key="lookup-progress"):
            st.header("관련 공시를 확인하고 있습니다", anchor=False)
            st.write("완료되면 이 화면에 결과가 표시됩니다.")
            st.caption("처음 조회할 때는 자료 준비에 몇 분 걸릴 수 있습니다. 입력은 보관됩니다.")
            _run_lookup(
                pending.text,
                corp_code=pending.corp_code,
                corp_name=pending.corp_name,
                force_corp_sample=pending.force_corp_sample,
            )
        st.rerun()
    if _RESULT_KEY not in st.session_state and _ERROR_KEY not in st.session_state:
        st.info("표시할 조회 결과가 없습니다. 다시 조회하기에서 종목과 문장을 입력해주세요.")
        return
    draft = st.session_state.get(_DRAFT_KEY, _QueryDraft())
    _render_last_result(current_corp_code=draft.corp_code, current_text=draft.text)


def _input_page():
    return st.Page(_render_input_page, title="공시 입력", default=True)


def _result_page():
    return st.Page(_render_result_page, title="조회 결과", url_path="result")


def main() -> None:
    st.set_page_config(
        page_title="공시 리터러시 어시스턴트", page_icon="📋", initial_sidebar_state="collapsed"
    )
    prepare_terminal()
    page = st.navigation([_input_page(), _result_page()], position="hidden")
    is_result = page.url_path == "result"
    pending = is_result and _PENDING_KEY in st.session_state
    if not is_result:
        st.html('<div class="terminal-eyebrow">DISCLOSURE TERMINAL / 공시 단말기</div>')
    st.title(
        "공시 대조 중" if pending else "공시 대조 결과" if is_result else "공시 리터러시 어시스턴트"
    )
    st.caption(
        "공시 사실과 과거 화법 패턴만 보여줍니다. 매수·매도 추천, 목표가, 등급은 제공하지 "
        "않으며 최종 판단은 항상 사용자 몫입니다."
        if is_result
        else "종목을 선택하고 루머든 공시 문장이든 붙여넣으면 실제 공시 사실과 과거 화법 "
        "패턴만 보여줍니다. 매수·매도 추천, 목표가, 등급은 제공하지 않으며 최종 판단은 "
        "항상 사용자 몫입니다."
    )
    # 공통 프레임에 두어 페이지 이동 후에도 개발자 옵션의 값을 유지한다.
    with st.sidebar.expander("⚙️ 개발자 옵션", expanded=False):
        st.checkbox(
            "레이아웃 확인용 샘플로 강제 표시",
            value=False,
            key="_force_corp_sample",
            disabled=pending,
            help=(
                "팩트체크 엔진이 아직 안 붙었거나 일시적으로 못 쓸 때, 종목이 "
                "특정된 경우의 화면 레이아웃을 강제로 미리 볼 수 있습니다."
            ),
        )

    page.run()


if __name__ == "__main__":
    main()
