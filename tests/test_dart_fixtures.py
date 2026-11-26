"""A9 — 실제 DART 원문 픽스처(tests/fixtures/dart/, README 참고)로 chunk_disclosure 검증.

합성 픽스처(test_ingest_chunk_disclosure.py)는 구조만 재현한 것이고, 여기는 실제 API가
준 원문 그대로다 — 향후 파서를 바꿀 때 실측 케이스가 깨지지 않는지 잡아내는 회귀 테스트.
네트워크 호출 없음(파일에서 읽기만 함).
"""

from __future__ import annotations

from pathlib import Path

from disclosure_lit.dart.ingest import FALLBACK_SECTION_TITLE, chunk_disclosure

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "dart"


def _load(filename: str) -> bytes:
    return (FIXTURES_DIR / filename).read_bytes()


class TestFamilyAFixtures:
    def test_capital_increase_splits_into_titled_sections(self) -> None:
        raw = _load("family_a_capital_increase_20260910000580.xml")
        sections = chunk_disclosure(raw, rcept_no="20260910000580", corp_code="00000000")

        assert len(sections) == 2
        assert all(s.form_type == "유상증자" for s in sections)
        titles = [s.section_title for s in sections]
        assert "유상증자 결정" in titles

    def test_stake_disclosure_without_title_still_extracts_body(self) -> None:
        """이 문서의 <SECTION-1> 은 <TITLE> 자식이 없다 — section_title 은 FALLBACK 으로
        떨어지지만(구조를 아예 못 찾은 "(미분할)" 폴백과는 다름), 본문 텍스트는 그대로
        보존돼야 한다."""
        raw = _load("family_a_stake_disclosure_20260910000575.xml")
        sections = chunk_disclosure(raw, rcept_no="20260910000575", corp_code="00000000")

        assert len(sections) == 1
        assert sections[0].section_title == FALLBACK_SECTION_TITLE
        assert "(미분할)" not in sections[0].section_title
        assert sections[0].text  # 본문은 비어있지 않다
        assert sections[0].form_type == "임원ㆍ주요주주_지분보고"

    def test_treasury_stock_splits_correctly(self) -> None:
        raw = _load("family_a_treasury_stock_20260910000386.xml")
        sections = chunk_disclosure(raw, rcept_no="20260910000386", corp_code="00000000")

        assert len(sections) == 2
        assert all(s.form_type == "자기주식_취득" for s in sections)
        assert "자기주식취득 신탁계약 해지 결정" in [s.section_title for s in sections]


class TestFamilyBFixtures:
    def test_supply_contract_single_table(self) -> None:
        raw = _load("family_b_supply_contract_20260911800004.xml")
        sections = chunk_disclosure(raw, rcept_no="20260911800004", corp_code="00000000")

        assert len(sections) == 1
        assert sections[0].form_type == "공급계약_체결"
        assert "계약금액" in sections[0].text

    def test_agm_result_duplicate_numbering_does_not_collide(self) -> None:
        """이 문서 하나가 chunk_disclosure의 "<table> 단위 분리" 설계를 낳은 실제 사례다 —
        본문 표와 "세부내역" 표가 각각 독립적으로 1번부터 번호를 매긴다. 번호를 섹션
        키로 삼았다면 두 표의 "1."이 충돌했을 것이다."""
        raw = _load("family_b_agm_result_20260910800272.xml")
        sections = chunk_disclosure(raw, rcept_no="20260910800272", corp_code="00000000")

        assert len(sections) == 3
        titles = [s.section_title for s in sections]
        assert "임시주주총회 결과" in titles
        assert "주주총회 안건 세부내역" in titles

        main_section = next(s for s in sections if s.section_title == "임시주주총회 결과")
        detail_section = next(s for s in sections if s.section_title == "주주총회 안건 세부내역")
        # 두 표의 내용이 섞이지 않았는지 확인
        assert "의결권행사기준일" in main_section.text
        assert "의결권행사기준일" not in detail_section.text

    def test_equity_investment_splits_into_multiple_tables(self) -> None:
        raw = _load("family_b_equity_investment_20260910800627.xml")
        sections = chunk_disclosure(raw, rcept_no="20260910800627", corp_code="00000000")

        assert len(sections) == 3
        assert all(s.form_type == "타법인출자_취득" for s in sections)
        assert any("H&L Advanced" in s.text for s in sections)  # & 문자 보존 확인
