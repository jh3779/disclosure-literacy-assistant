"""dictionary.company_trend 단위 테스트. 임베딩 불필요 — 순수 날짜/카운트 로직."""

from __future__ import annotations

from disclosure_lit.dart.ingest import DisclosureSection
from disclosure_lit.dictionary.company_trend import compute_company_trend


def _section(corp_code: str, rcept_no: str, form_type: str, rcept_dt: str) -> DisclosureSection:
    return DisclosureSection(
        rcept_no=rcept_no,
        corp_code=corp_code,
        form_type=form_type,
        section_title="제목",
        text="본문",
        rcept_dt=rcept_dt,
    )


class TestComputeCompanyTrend:
    def test_too_few_disclosures_returns_empty(self) -> None:
        sections = [_section("C1", f"R{i}", "유상증자", "20260101") for i in range(3)]

        assert compute_company_trend("C1", sections) == []

    def test_ignores_other_companies(self) -> None:
        sections = [_section("C1", f"R{i}", "유상증자", "20260101") for i in range(5)]
        sections += [_section("C2", f"O{i}", "감자", "20260901") for i in range(5)]

        result = compute_company_trend("C2", sections, recent_months=3)

        assert result == []  # C2는 전부 같은 시점이라 최근/과거 구분이 안 됨(비교 불가)

    def test_increasing_form_type_detected(self) -> None:
        # 과거 구간(6개월 전): 유상증자만 5건. 최근 구간(1개월 전): 감자만 5건.
        sections = [_section("C1", f"OLD{i}", "유상증자", "20260101") for i in range(5)]
        sections += [_section("C1", f"NEW{i}", "감자", "20260801") for i in range(5)]

        result = compute_company_trend("C1", sections, recent_months=3)

        by_type = {item.form_type: item for item in result}
        assert by_type["감자"].is_increasing is True
        assert by_type["감자"].recent_rate == 1.0
        assert by_type["감자"].baseline_rate == 0.0
        assert by_type["유상증자"].is_increasing is False
        assert by_type["유상증자"].recent_rate == 0.0
        assert by_type["유상증자"].baseline_rate == 1.0

    def test_duplicate_sections_of_same_disclosure_counted_once(self) -> None:
        """한 공시(rcept_no)에 섹션이 여러 개여도(A6 서식별 청킹) 공시 1건으로만
        세야 한다."""
        sections = [
            _section("C1", "R1", "유상증자", "20260101"),
            _section("C1", "R1", "유상증자", "20260101"),  # 같은 공시의 다른 섹션
            *[_section("C1", f"R{i}", "유상증자", "20260102") for i in range(2, 5)],
            *[_section("C1", f"N{i}", "감자", "20260801") for i in range(3)],
        ]

        result = compute_company_trend("C1", sections, recent_months=3)

        by_type = {item.form_type: item for item in result}
        assert by_type["유상증자"].baseline_count == 4  # R1(중복 제거)+R2+R3+R4
        assert by_type["감자"].recent_count == 3

    def test_missing_rcept_dt_is_ignored(self) -> None:
        sections = [_section("C1", f"R{i}", "유상증자", "20260101") for i in range(5)]
        sections.append(_section("C1", "R_NODATE", "감자", ""))

        result = compute_company_trend("C1", sections, recent_months=3)

        assert result == []  # 날짜 있는 건 전부 같은 시점(비교 불가), 날짜 없는 건 무시됨

    def test_sorted_by_largest_increase_first(self) -> None:
        sections = [_section("C1", f"OLD{i}", "유상증자", "20260101") for i in range(10)]
        sections += [_section("C1", f"NEWA{i}", "감자", "20260801") for i in range(8)]
        sections += [_section("C1", f"NEWB{i}", "회사합병", "20260801") for i in range(2)]

        result = compute_company_trend("C1", sections, recent_months=3)

        assert result[0].form_type == "감자"  # 증가폭이 가장 큼
