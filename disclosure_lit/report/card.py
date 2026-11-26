"""L2a·L2b 결과를 하나의 리포트 카드로 조립 — L3. 담당: C(우지훈)."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from disclosure_lit.dart.client import CorpCodeIndex
from disclosure_lit.dictionary.stats import ClusterStats
from disclosure_lit.factcheck.claim import Claim, extract_claim
from disclosure_lit.factcheck.verify import FactCheckResult
from disclosure_lit.pipeline import run_dictionary_lookup, run_factcheck
from disclosure_lit.retrieval.company_index import CompanyDisclosureIndex
from disclosure_lit.retrieval.phrase_index import PhraseClusterIndex

_logger = logging.getLogger(__name__)


@dataclass
class ReportCard:
    fact_check: FactCheckResult | None
    phrase_stats: ClusterStats | None


def resolve_claim(text: str) -> Claim:
    """입력 문장을 Claim으로 바꾼다. `extract_claim`(B 담당)이 아직 스텁이면 종목
    미특정 Claim으로 폴백한다.

    폴백이 필요한 이유(PR #33 리뷰 지적, @powercodingman): 이 폴백 없이
    `NotImplementedError`를 그대로 올리면 `build_report`가 첫 줄에서 죽어버려서, 어떤
    입력을 넣든 호출부가 똑같은 화면만 보게 된다 — 리팩터링 전에는 살아있던 "문구 단독
    검색" 경로를 데모에서 확인할 방법이 사라진다.

    호출부(demo/app.py)도 "어느 경로가 시도됐는지"에 맞는 샘플을 고를 때 이 함수를
    그대로 쓴다 — 같은 판단 로직을 두 군데 두지 않기 위함.
    """
    try:
        return extract_claim(text)
    except NotImplementedError:
        _logger.warning("extract_claim()이 아직 스텁입니다 — 종목 미특정 Claim으로 폴백합니다.")
        return Claim(corp_name=None, corp_code=None, keyword=text, raw_text=text)


def build_report(
    text: str,
    *,
    company_index: CompanyDisclosureIndex,
    corp_code_index: CorpCodeIndex,
    phrase_index: PhraseClusterIndex,
    corp_code_override: str | None = None,
) -> ReportCard:
    """입력 문장 하나로 L2a(팩트체크)/L2b(화법통계)를 자동 분기해 결합한다.

    원래 이 결합 로직은 demo/app.py(L4)의 `_build_card`에 있었다 — CLAUDE.md 아키텍처
    표는 "L2a 결과에 L2b를 자동 주석으로 결합"이 L3(이 파일)의 몫이라고 적어뒀는데
    실제 구현 위치가 어긋나 있었다(PR #33 리뷰 지적, @powercodingman). 다른 소비자(API,
    eval 스크립트 등)도 같은 결합이 필요하면 이 함수 하나로 재사용할 수 있도록 여기로
    옮겼다.

    종목이 특정되면(`claim.corp_code` 또는 `claim.corp_name`) 팩트체크를 돌리고, 그
    결과에 포함된 공시 원문에서 상투구를 찾아 자동 주석으로 붙인다(첫 매칭에서 멈춘다
    — 여러 화법 통계를 한 카드에 다 붙이면 오히려 산만해진다). 종목이 특정되지 않으면
    입력 문장 자체를 화법 문구 단독 검색으로 취급한다(DESIGN.md 핵심 기능 흐름).

    `extract_claim`은 아직 구현 전이어도 `resolve_claim()`이 종목 미특정 Claim으로
    폴백하므로 여기서 죽지 않는다. 반면 `run_factcheck`/`run_dictionary_lookup`(B의
    엔진)이 미구현이면 `NotImplementedError`가 그대로 올라간다 — 호출부(demo/app.py)가
    샘플 데이터 표시 여부를 판단하는 데 쓴다.

    `corp_code_override`(2026-09-18 추가): 호출부가 이미 정확한 corp_code를 알고
    있을 때(예: 데모 UI의 종목 선택 드롭다운) 넘긴다 — `extract_claim`의 회사명
    추출이나 `CorpCodeIndex.find_by_name()`의 이름 매칭(법인격 표시·오타 등으로
    실패할 수 있음)에 의존하지 않고 바로 그 corp_code로 조회한다. `keyword`(검색
    질의문)는 여전히 `resolve_claim()`이 뽑은 값을 쓴다 — 회사 식별과 검색 질의
    추출은 서로 다른 문제라, override는 corp_code에만 좁게 적용한다.
    """
    claim = resolve_claim(text)
    if corp_code_override:
        claim.corp_code = corp_code_override

    fact_check: FactCheckResult | None = None
    phrase_stats: ClusterStats | None = None

    if claim.corp_code or claim.corp_name:
        fact_check = run_factcheck(
            claim, company_index=company_index, corp_code_index=corp_code_index
        )
        for item in fact_check.items:
            if item.supporting_section is None:
                continue
            phrase_stats = run_dictionary_lookup(
                item.supporting_section.text, phrase_index=phrase_index
            )
            if phrase_stats is not None:
                break
    else:
        phrase_stats = run_dictionary_lookup(text, phrase_index=phrase_index)

    return ReportCard(fact_check=fact_check, phrase_stats=phrase_stats)
