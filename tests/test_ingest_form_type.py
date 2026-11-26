"""A5 — normalize_form_type 단위 테스트. 방향성이 갈리는 케이스(취득/처분 등)를 중점 검증."""

from __future__ import annotations

import pytest

from disclosure_lit.dart.ingest import FALLBACK_FORM_TYPE, normalize_form_type


class TestBasicCategories:
    @pytest.mark.parametrize(
        ("report_nm", "expected"),
        [
            ("유상증자결정", "유상증자"),
            ("무상증자결정", "무상증자"),
            ("감자결정", "감자"),
            ("전환사채권발행결정", "전환사채_발행"),
            ("신주인수권부사채권발행결정", "신주인수권부사채_발행"),
            ("영업정지", "영업정지"),
            ("양해각서(MOU)체결", "MOU"),
            ("임원ㆍ주요주주특정증권등소유상황보고서", "임원ㆍ주요주주_지분보고"),
            ("주식등의대량보유상황보고서(일반)", "대량보유_지분보고"),
            ("기업설명회(IR)개최(안내공시)", "IR_기업설명회"),
            ("분기보고서", "정기공시"),
            ("완전히새로운유형의공시서식", FALLBACK_FORM_TYPE),
        ],
    )
    def test_maps_known_titles(self, report_nm: str, expected: str) -> None:
        assert normalize_form_type(report_nm) == expected


class TestDirectionSensitiveCasesStayApart:
    """취득/처분처럼 방향이 반대인 이벤트는 같은 키워드를 공유해도 절대 합치면 안 된다."""

    def test_treasury_stock_acquisition_vs_disposal(self) -> None:
        acquire = normalize_form_type("자기주식취득결정")
        dispose = normalize_form_type("자기주식처분결정")
        assert acquire == "자기주식_취득"
        assert dispose == "자기주식_처분"
        assert acquire != dispose

    def test_other_corp_stake_acquisition_vs_disposal(self) -> None:
        acquire = normalize_form_type("타법인주식및출자증권취득결정")
        dispose = normalize_form_type("타법인주식및출자증권처분결정")
        assert acquire == "타법인출자_취득"
        assert dispose == "타법인출자_처분"
        assert acquire != dispose

    def test_actual_control_change_vs_routine_ownership_report(self) -> None:
        """'최대주주 변경'(실제 지배권 이전)과 '최대주주 등 소유주식 변동 신고서'(정기
        지분 보고)는 전혀 다른 무게의 이벤트인데 '최대주주'라는 키워드를 공유한다."""
        actual_change = normalize_form_type("최대주주변경을수반하는주식양수도계약체결결정")
        routine_report = normalize_form_type("최대주주등소유주식변동신고서")
        assert actual_change == "최대주주_변경"
        assert routine_report == "최대주주_지분보고"
        assert actual_change != routine_report

    def test_paid_vs_free_capital_increase_not_confused(self) -> None:
        assert normalize_form_type("유상증자결정") == "유상증자"
        assert normalize_form_type("무상증자결정") == "무상증자"

    def test_supply_contract_termination_vs_conclusion(self) -> None:
        assert normalize_form_type("단일판매ㆍ공급계약체결") == "공급계약_체결"
        assert normalize_form_type("단일판매ㆍ공급계약해지") == "공급계약_해지"

    def test_supply_contract_neither_conclusion_nor_termination_falls_back(self) -> None:
        """리뷰 지적(PR #25, @jh3779): '공급계약_체결' 규칙이 '체결'을 확인하지 않는
        catch-all이면, 방향이 다른("조건변경" 등) 제목까지 '체결'로 잘못 묶인다.
        실측(2026-01~03, 06~09월 코스피+코스닥 80건)으로는 '체결'/'해지' 외 변형이
        없었지만, 앞으로 새 변형이 나와도 안전하도록 '체결'을 명시적으로 요구한다."""
        assert normalize_form_type("단일판매ㆍ공급계약 조건변경") == FALLBACK_FORM_TYPE
        assert normalize_form_type("단일판매ㆍ공급계약 취소") == FALLBACK_FORM_TYPE

    def test_trust_contract_acquisition_vs_termination(self) -> None:
        assert normalize_form_type("신탁계약에의한취득상황보고서") == "신탁계약_취득"
        assert normalize_form_type("신탁계약해지결과보고서") == "신탁계약_해지"

    def test_trading_halt_vs_resumption(self) -> None:
        assert normalize_form_type("주권매매거래정지") == "거래정지"
        assert normalize_form_type("주권매매거래정지해제") == "거래재개"

    def test_convertible_bond_issuance_vs_early_acquisition_vs_sale(self) -> None:
        assert normalize_form_type("전환사채권발행결정") == "전환사채_발행"
        assert (
            normalize_form_type("주요사항보고서(자기전환사채만기전취득결정)") == "전환사채_조기취득"
        )
        assert normalize_form_type("주요사항보고서(자기전환사채매도결정)") == "전환사채_매도"


class TestNoiseHandling:
    def test_strips_correction_prefix_tag(self) -> None:
        assert (
            normalize_form_type("[기재정정]임원ㆍ주요주주특정증권등소유상황보고서")
            == "임원ㆍ주요주주_지분보고"
        )

    def test_strips_multiple_prefix_tags(self) -> None:
        assert normalize_form_type("[기재정정][첨부정정]유상증자결정") == "유상증자"

    def test_strips_trailing_whitespace(self) -> None:
        noisy = "최대주주등소유주식변동신고서              "
        assert normalize_form_type(noisy) == "최대주주_지분보고"

    def test_unmatched_title_returns_explicit_fallback_not_none(self) -> None:
        result = normalize_form_type("생전 처음 보는 서식")
        assert result == FALLBACK_FORM_TYPE
        assert result is not None
