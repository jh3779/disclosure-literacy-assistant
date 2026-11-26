"""A11 — extract_cliche_candidates.py 단위 테스트. 담당: A(이승노).

scripts/ 는 패키지가 아니라서(`python scripts/extract_cliche_candidates.py` 로 실행)
tests/test_build_corpus.py 와 같은 방식으로 sys.path 에 scripts/ 를 넣고 모듈로 임포트한다.
DART API 호출이 없는 순수 텍스트 처리 로직이라 합성 섹션 딕셔너리만으로 검증한다.
"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from extract_cliche_candidates import (  # noqa: E402
    extract_candidates,
    is_candidate_sentence,
    normalize_sentence,
)


def _section(corp_code: str, text: str, *, rcept_no: str = "R1", form_type: str = "MOU") -> dict:
    return {"corp_code": corp_code, "rcept_no": rcept_no, "form_type": form_type, "text": text}


class TestNormalizeSentence:
    def test_replaces_digit_runs_with_placeholder(self) -> None:
        assert normalize_sentence("결정일자 2025-08-26 입니다.") == "결정일자 # 입니다."

    def test_different_dates_normalize_to_same_key(self) -> None:
        a = normalize_sentence("2025-08-26 IR 개최 예정입니다.")
        b = normalize_sentence("2025-07-25 IR 개최 예정입니다.")
        assert a == b

    def test_collapses_whitespace(self) -> None:
        assert normalize_sentence("여러   공백   문장") == "여러 공백 문장"


class TestIsCandidateSentence:
    def test_rejects_too_short(self) -> None:
        assert is_candidate_sentence("짧음") is False

    def test_rejects_digit_heavy_table_row(self) -> None:
        # 표 데이터처럼 숫자 비율이 높은 조각(주주명부 지분율 나열 등)은 화법 후보가 아니다.
        assert is_candidate_sentence("51,176,250 51.05 46,176,250 46.06 8,600 0.01") is False

    def test_accepts_prose_sentence(self) -> None:
        sentence = "중장기 성장 동력 확보를 위해 본 계약을 체결하였습니다."
        assert is_candidate_sentence(sentence) is True

    def test_rejects_fragment_without_terminal_punctuation(self) -> None:
        # 문장 분리기의 마지막 잔여 조각(제목만 있고 서술어가 없는 텍스트) 방지.
        assert is_candidate_sentence("이것은 마침표가 없는 긴 제목성 텍스트 조각입니다") is False

    def test_rejects_periodic_report_outline_marker_fused_to_header(self) -> None:
        """실측(11개사 드라이런) 최상위 노이즈 패턴 — "...관한 사항 가."처럼 정기공시
        순번 마커(가/나/다...)가 문장 분리 과정에서 앞 헤더에 들러붙은 잔재."""
        assert is_candidate_sentence("감사제도에 관한 사항 가.") is False
        assert is_candidate_sentence("주요 제품 및 서비스 나.") is False

    def test_rejects_numeral_list_marker_fused_to_header(self) -> None:
        """실측(18개사 드라이런) — 숫자 순번 마커("...관한 사항 1.")도 같은 패턴."""
        assert is_candidate_sentence("증권의 발행을 통한 자금조달에 관한 사항 1.") is False

    def test_rejects_sub_numbered_list_marker(self) -> None:
        """실측 — "7-1."처럼 하위번호가 붙은 마커. 숫자·하이픈·콜론만으로 이뤄진 어절이면
        제외한다(_NUMBER_RUN_RE 가 "-"도 숫자 구간에 포함해 정규화 후엔 "#"로 뭉개진다)."""
        assert is_candidate_sentence("증권의 발행을 통한 자금조달에 관한 사항 7-1.") is False

    def test_rejects_single_latin_letter_marker(self) -> None:
        """실측 — "참조 X."처럼 로마자 한 글자 각주 마커."""
        assert is_candidate_sentence("타법인출자 현황(상세) 참조 X.") is False

    def test_rejects_multi_letter_ascii_roman_numeral_marker(self) -> None:
        """실측(101개사 최종 실행) — "참조 IX."처럼 두 글자 이상 아스키 로마숫자 마커도
        걸러야 한다. 단순 "라틴 알파벳뿐" 판정이면 "IR" 같은 진짜 단어도 걸러버리므로
        로마숫자 문법 검증(_ASCII_ROMAN_NUMERAL)을 따로 거친다."""
        assert is_candidate_sentence("타법인출자 현황(상세) 참조 IX.") is False

    def test_rejects_unicode_roman_numeral_marker(self) -> None:
        """실측(101개사 최종 실행) — "결손금처리액 Ⅲ."처럼 유니코드 로마숫자 마커."""
        assert is_candidate_sentence("결손금처리액에 관한 사항 Ⅲ.") is False

    def test_keeps_real_word_that_looks_like_stray_latin_letters(self) -> None:
        """ "IR"처럼 진짜 영문 단어가 문장 끝 어절이어도, 로마숫자 문법에 안 맞으면(I 다음
        R은 유효한 로마숫자 기호가 아님) 마커로 오인되지 않는다 — 마지막 어절만 검사하는
        경계 조건이라 일부러 문장 끝에 "IR"을 배치했다."""
        sentence = "자세한 사항은 당사 홈페이지에 게재된 자료를 참고하시기 바랍니다 IR."
        assert is_candidate_sentence(sentence) is True

    def test_rejects_section_title_brackets(self) -> None:
        """【 】는 DART 섹션 제목에만 쓰이는 관례적 표기라 화법일 수 없다."""
        assert is_candidate_sentence("【 대표이사 등의 확인 】 사항입니다.") is False

    def test_rejects_sentence_with_kifrs_strong_marker(self) -> None:
        """실측 — "기준서"/"조기적용" 등은 회계기준 원문 외에는 거의 안 쓰여 1회 등장만으로도
        제외한다. form_type 필터(정기공시 제외)만으로는 주주총회_소집·증권발행_신고 등에
        첨부된 재무제표 주석까지는 못 걸렀다(25개사 드라이런 실측)."""
        assert (
            is_candidate_sentence("해당 기준서의 개정이 재무제표에 미치는 중요한 영향은 없습니다.")
            is False
        )
        assert is_candidate_sentence("동 개정사항은 조기적용이 허용됩니다.") is False

    def test_rejects_sentence_with_two_or_more_kifrs_weak_terms(self) -> None:
        sentence = "금융자산을 제거할 때에는 인식한 기타포괄손익누계액을 자본에서 재분류합니다."
        assert is_candidate_sentence(sentence) is False

    def test_keeps_sentence_with_single_weak_kifrs_term(self) -> None:
        # 문맥상 일반 서술에도 나올 수 있는 단어(공정가치)가 하나만 있으면 화법 후보로 남긴다.
        sentence = "이번 유상증자의 발행가액은 공정가치를 반영해 산정되었습니다."
        assert is_candidate_sentence(sentence) is True

    def test_accepts_sentence_that_legitimately_ends_in_da_marker_lookalike(self) -> None:
        # "다."로 끝나되 그 앞이 진짜 서술어인 경우(마지막 어절이 한 글자가 아님)는 유지한다.
        sentence = "중장기 성장 동력 확보를 위해 본 계약을 지속적으로 추진하고 있습니다."
        assert is_candidate_sentence(sentence) is True


class TestExtractCandidates:
    def test_filters_below_min_companies_threshold(self) -> None:
        sections = [
            _section("C1", "선제적 재무구조 개선을 추진하고 있습니다."),
            _section("C2", "선제적 재무구조 개선을 추진하고 있습니다."),
        ]

        candidates = extract_candidates(sections, min_companies=3)

        assert candidates == []

    def test_keeps_sentence_appearing_across_enough_companies(self) -> None:
        sentence = "중장기 성장 동력 확보를 위해 본 계약을 체결하였습니다."
        sections = [_section(f"C{i}", sentence) for i in range(3)]

        candidates = extract_candidates(sections, min_companies=3, max_company_fraction=1.0)

        assert len(candidates) == 1
        assert candidates[0].distinct_company_count == 3
        assert candidates[0].total_occurrences == 3

    def test_same_company_repeating_counts_once_toward_company_count(self) -> None:
        """한 회사가 자기 공시 안에서 반복하는 건 "여러 회사가 공유하는 화법"이 아니다."""
        sentence = "중장기 성장 동력 확보를 위해 본 계약을 체결하였습니다."
        sections = [
            _section("C1", sentence, rcept_no="R1"),
            _section("C1", sentence, rcept_no="R2"),
            _section("C1", sentence, rcept_no="R3"),
        ]

        candidates = extract_candidates(sections, min_companies=2)

        assert candidates == []  # distinct_company_count=1 이라 min_companies=2 미달

    def test_numbers_only_differ_still_grouped_as_one_candidate(self) -> None:
        # 날짜가 문장 전체를 지배하는 짧은 조각("2025-08-26 IR 개최")은 표 필드 값에 가까워
        # is_candidate_sentence 가 걸러낸다 — 실제 화법 문장처럼 날짜가 서술 안에 묻힌 경우로 구성.
        sections = [
            _section("C1", "본 계약은 2025-08-26 자로 체결되어 즉시 효력이 발생합니다."),
            _section("C2", "본 계약은 2025-07-25 자로 체결되어 즉시 효력이 발생합니다."),
            _section("C3", "본 계약은 2026-01-10 자로 체결되어 즉시 효력이 발생합니다."),
        ]

        candidates = extract_candidates(sections, min_companies=3, max_company_fraction=1.0)

        assert len(candidates) == 1
        assert candidates[0].distinct_company_count == 3

    def test_sorted_by_company_count_descending(self) -> None:
        common = "중장기 성장 동력 확보를 위해 본 계약을 체결하였습니다."
        rare = "선제적 재무구조 개선을 지속적으로 추진하고 있습니다."
        sections = [_section(f"C{i}", common) for i in range(5)] + [
            _section(f"C{i}", rare) for i in range(5, 8)
        ]

        candidates = extract_candidates(sections, min_companies=2, max_company_fraction=1.0)

        assert candidates[0].normalized.startswith("중장기")
        assert candidates[1].normalized.startswith("선제적")

    def test_samples_capped_and_traceable(self) -> None:
        sentence = "중장기 성장 동력 확보를 위해 본 계약을 체결하였습니다."
        sections = [_section(f"C{i}", sentence, rcept_no=f"R{i}") for i in range(10)]

        candidates = extract_candidates(
            sections, min_companies=3, max_samples=2, max_company_fraction=1.0
        )

        assert len(candidates[0].samples) == 2
        assert candidates[0].samples[0]["corp_code"] == "C0"
        assert candidates[0].samples[0]["rcept_no"] == "R0"

    def test_form_types_collected_from_all_matching_sections(self) -> None:
        sentence = "중장기 성장 동력 확보를 위해 본 계약을 체결하였습니다."
        sections = [
            _section("C1", sentence, form_type="MOU"),
            _section("C2", sentence, form_type="유상증자"),
            _section("C3", sentence, form_type="MOU"),
        ]

        candidates = extract_candidates(sections, min_companies=3, max_company_fraction=1.0)

        assert candidates[0].form_types == ["MOU", "유상증자"]

    def test_excludes_sentence_appearing_in_too_high_a_company_fraction(self) -> None:
        """K-IFRS 어휘 필터로 못 잡는 법정 의무 문구용 백업 필터 — 회사 수 비율 자체가
        상한을 넘으면(기본 50%) "회사가 선택하는 화법"이 아니라 "거의 전부가 쓰는 문구"로
        보고 제외한다. 어휘를 끝없이 추가하는 두더지잡기 대신 일반화된 신호를 쓴다."""
        sentence = "본 손상차손은 신용위험 평가모형에 따라 매 보고기간 말 재측정됩니다."
        # 10개사 중 8개사(80%)에서 등장 — 기본 상한 50%를 넘는다.
        sections = [_section(f"C{i}", sentence) for i in range(8)] + [
            _section(f"C{i}", "무관한 다른 텍스트 채우기용 섹션입니다.") for i in range(8, 10)
        ]

        candidates = extract_candidates(sections, min_companies=3, max_company_fraction=0.5)

        assert candidates == []

    def test_keeps_sentence_under_max_company_fraction(self) -> None:
        sentence = "중장기 성장 동력 확보를 위해 본 계약을 체결하였습니다."
        # 10개사 중 3개사(30%)만 등장 — 기본 상한 50% 이내.
        sections = [_section(f"C{i}", sentence) for i in range(3)] + [
            _section(f"C{i}", "무관한 다른 텍스트 채우기용 섹션입니다.") for i in range(3, 10)
        ]

        candidates = extract_candidates(sections, min_companies=3, max_company_fraction=0.5)

        assert len(candidates) == 1

    def test_excludes_periodic_report_boilerplate_by_default(self) -> None:
        """정기공시(사업보고서 등)는 K-IFRS 회계기준 법정 의무 문구가 대부분이라 기본
        제외한다 — 실측(20개사 드라이런)에서 포함 시 상위 후보 전부를 회계 주석이 차지했다."""
        sentence = "해당 기준서의 개정이 재무제표에 미치는 중요한 영향은 없습니다."
        sections = [_section(f"C{i}", sentence, form_type="정기공시") for i in range(5)]

        candidates = extract_candidates(sections, min_companies=3)

        assert candidates == []

    def test_excluded_form_type_is_configurable(self) -> None:
        sentence = "중장기 성장 동력 확보를 위해 본 계약을 체결하였습니다."
        sections = [_section(f"C{i}", sentence, form_type="MOU") for i in range(3)]

        candidates = extract_candidates(
            sections, min_companies=3, excluded_form_types=frozenset({"MOU"})
        )

        assert candidates == []

    def test_multiple_sentences_in_one_section_each_counted(self) -> None:
        text = (
            "중장기 성장 동력 확보를 위해 본 계약을 체결하였습니다. "
            "선제적 재무구조 개선을 지속적으로 추진하고 있습니다."
        )
        sections = [_section(f"C{i}", text) for i in range(3)]

        candidates = extract_candidates(sections, min_companies=3, max_company_fraction=1.0)

        assert len(candidates) == 2
