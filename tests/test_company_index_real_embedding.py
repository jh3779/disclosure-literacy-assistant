"""A7 — CompanyDisclosureIndex 를 실제 dragonkue/BGE-m3-ko 로 검증.

tests/test_company_disclosure_index.py 는 가짜(결정론적) 임베딩으로 랭킹 로직을
검증하고, 여기는 진짜 모델로 "의미적으로 관련되면 키워드가 없어도 찾아지는지"를
확인한다. `slow` 마커라 기본 pytest 실행에서 제외된다(로컬 모델 로딩 필요).
"""

from __future__ import annotations

import pytest

from disclosure_lit.dart.ingest import DisclosureSection
from disclosure_lit.retrieval.company_index import CompanyDisclosureIndex


def _section(rcept_no: str, title: str, text: str) -> DisclosureSection:
    return DisclosureSection(
        rcept_no=rcept_no, corp_code="C1", form_type="유상증자", section_title=title, text=text
    )


@pytest.mark.slow
class TestRealEmbeddingSemanticSearch:
    def test_finds_semantically_related_section_without_exact_keyword(self) -> None:
        index = CompanyDisclosureIndex()
        index.index(
            [
                _section(
                    "R1",
                    "신주 발행 결정",
                    "당사는 이사회 결의를 통해 제3자배정 방식의 신주를 발행하기로 하였습니다.",
                ),
                _section("R2", "오늘의 인사", "오늘 날씨가 맑고 화창합니다."),
            ]
        )

        # "유상증자"라는 단어 자체는 두 섹션 어디에도 없다 — 순수 키워드 검색이었다면
        # 둘 다 안 걸렸을 것. 임베딩이 "신주 발행"을 의미적으로 찾아내야 한다.
        result = index.search("C1", "유상증자")

        assert len(result) >= 1
        assert result[0].rcept_no == "R1"

    def test_keyword_bonus_still_wins_over_pure_semantic_match(self) -> None:
        index = CompanyDisclosureIndex()
        index.index(
            [
                _section("R_exact", "유상증자 결정", "신주 발행 관련 세부 내용입니다."),
                _section("R_related", "자금 조달 계획", "신주를 발행해 운영자금을 마련합니다."),
            ]
        )

        result = index.search("C1", "유상증자")
        rcept_nos = [s.rcept_no for s in result]
        assert rcept_nos[0] == "R_exact"
