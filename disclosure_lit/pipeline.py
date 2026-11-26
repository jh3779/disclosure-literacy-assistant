"""L1(retrieval) 결과를 L2a(factcheck)/L2b(dictionary)로 넘기는 오케스트레이션.

Issue #32/DEC-005(2026-09-11, 우지훈 확정)에서 합의된 인터페이스를 구현한다:

- `run_factcheck`: `company_index.search()`로 top_k=10 후보를 뽑아 상위 5개만
  `verify_claim()`에 전달(LLM 컨텍스트/노이즈 관리). `claim.corp_code`가 비어 있으면
  `resolve_corp_code()`로 `claim.corp_name`을 `corp_code_index`에서 찾아 해석하고,
  그래도 못 찾으면 크래시 대신 빈 `FactCheckResult`를 반환한다.
- `run_dictionary_lookup`: 클러스터 매칭 실패(`None`)면 그대로 `None`을 반환한다 —
  호출부(report/card.py, demo/app.py)가 그 경우 화법통계 섹션을 생략하기로 합의됨.

B(김가빈)의 실제 엔진(`verify_claim`/`compute_stats`/`PhraseClusterIndex.match`)은 아직
`NotImplementedError` 스텁이다 — 이 파일은 그 엔진들을 연결하는 배선만 담당한다.
"""

from __future__ import annotations

import logging

from disclosure_lit.config import settings
from disclosure_lit.dart.client import CorpCodeIndex
from disclosure_lit.dictionary.stats import ClusterStats, compute_stats
from disclosure_lit.factcheck.claim import Claim
from disclosure_lit.factcheck.verify import FactCheckResult, verify_claim
from disclosure_lit.retrieval.company_index import CompanyDisclosureIndex
from disclosure_lit.retrieval.phrase_index import PhraseClusterIndex

_logger = logging.getLogger(__name__)

_SEARCH_TOP_K = 10
_VERIFY_MAX_SECTIONS = 5
# 지금은 company_index.search()가 rcept_dt 최신순 정렬만 해서(임베딩 랭킹 미구현,
# retrieval/company_index.py 참고) top_k=10으로 받아 5개로 자르나 top_k=5로 바로
# 받으나 결과가 같다 — 그래도 10으로 유지하는 이유(리뷰 지적, PR #34, @powercodingman):
# PR #35에서 임베딩+키워드 하이브리드 스코어링이 들어오면 "느슨하게 넓게 뽑은 뒤 상위
# 몇 개만 LLM에 넘긴다"는 이 구조가 그대로 의미를 갖는다. 그 전까지는 substring 스캔
# 5건이 낭비되지만 상수 하나 바꾸는 것보다 지금 구조를 유지하는 쪽을 택했다.


def resolve_corp_code(claim: Claim, corp_code_index: CorpCodeIndex) -> str | None:
    """claim에 corp_code가 없으면 corp_name으로 corp_code_index에서 찾는다.

    `extract_claim()`(LLM)이 루머 문장에서 종목명은 뽑아도 DART 내부 corp_code(8자리)까지
    직접 맞출 거라 기대하기 어렵다 — 그래서 별도 헬퍼로 뺐다(Issue #32/DEC-005).
    """
    if claim.corp_code:
        return claim.corp_code
    if claim.corp_name:
        # find_by_name 은 정규화 없는 dict exact-match다(dart/client.py::CorpCodeIndex).
        # extract_claim(LLM)이 앞뒤 공백을 붙여 뽑아도 매칭이 실패하지 않도록 최소한의
        # 정규화만 해준다 — 리뷰 지적(PR #34, @powercodingman): 이걸 안 하면 "공시 없음"과
        # "이름 매칭 실패"를 사용자가 구분할 방법이 없어진다.
        entry = corp_code_index.find_by_name(claim.corp_name.strip())
        if entry is not None:
            return entry.corp_code
    return None


def run_factcheck(
    claim: Claim,
    *,
    company_index: CompanyDisclosureIndex,
    corp_code_index: CorpCodeIndex,
) -> FactCheckResult:
    """company_index.search() 로 찾은 공시 섹션을 verify_claim 에 넘겨 대조한다.

    corp_code를 해석 못 하면 unresolved_company 오류 상태를 반환한다.
    설정된 관련성 문턱은 검색 점수에 적용한다. 기본값은 실측 검증 전까지 비활성화.
    """
    corp_code = resolve_corp_code(claim, corp_code_index)
    if corp_code is None:
        # 회사 해석 실패를 관련 공시 없음으로 표시하지 않는다(#130).
        _logger.warning(
            "종목 해석 실패 — 빈 팩트체크 결과를 반환합니다. corp_name=%r, keyword=%r",
            claim.corp_name,
            claim.keyword,
        )
        return FactCheckResult(
            claim=claim.raw_text, items=[], status="error", reason="unresolved_company"
        )

    if settings.factcheck_min_relevance_score is None:
        candidates = company_index.search(corp_code, claim.keyword, top_k=_SEARCH_TOP_K)
        return verify_claim(claim.keyword, candidates[:_VERIFY_MAX_SECTIONS])
    hits = company_index.search_with_scores(corp_code, claim.keyword, top_k=_SEARCH_TOP_K)
    hits = hits[:_VERIFY_MAX_SECTIONS]
    if any(hit.score is None for hit in hits):
        # 점수 없는 결과 = 검색어가 비어 최신순으로 돌려준 것(company_index.py
        # `search_with_scores()`의 `if not keyword` 분기). 비교할 관련성 점수가
        # 애초에 없는 것이지 관련성이 낮은 게 아니라, "low_relevance"로 기권하면
        # 사용자에게 틀린 이유를 말하게 된다. 문턱만 끄고 나머지는 문턱 미설정
        # 경로와 똑같이 진행한다 — 설정 하나가 결과의 유무까지 바꾸면 안 된다.
        return verify_claim(claim.keyword, [hit.section for hit in hits])
    return verify_claim(
        claim.keyword,
        [hit.section for hit in hits],
        relevance_scores=[hit.score for hit in hits],
        min_relevance_score=settings.factcheck_min_relevance_score,
    )


def run_dictionary_lookup(phrase: str, *, phrase_index: PhraseClusterIndex) -> ClusterStats | None:
    """phrase_index.match() 로 상투구 클러스터를 찾고, 매칭되면 compute_stats 로 통계를 반환한다.

    매칭 실패(None)는 그대로 None을 돌려준다 — report/card.py/demo/app.py가 이 경우
    화법통계 섹션 자체를 생략하기로 합의됨(Issue #32/DEC-005).
    """
    cluster = phrase_index.match(phrase)
    if cluster is None:
        return None
    return compute_stats(cluster.cluster_id)
