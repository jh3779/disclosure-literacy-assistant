"""L2b — extract_phrase_candidates / load_sections_jsonl 단위 테스트. 임베딩 불필요."""

from __future__ import annotations

import json
from pathlib import Path

from disclosure_lit.dart.ingest import DisclosureSection
from disclosure_lit.dictionary.candidates import (
    LEXICAL_MIN_COMPANIES,
    MAX_PHRASE_LENGTH,
    MIN_PHRASE_LENGTH,
    extract_phrase_candidates,
    load_all_local_sections,
    load_sections_jsonl,
    select_clustering_pool,
)


def _section(text: str, *, rcept_no: str = "R1", corp_code: str = "C1") -> DisclosureSection:
    return DisclosureSection(
        rcept_no=rcept_no, corp_code=corp_code, form_type="기타", section_title="본문", text=text
    )


class TestBulletPrefix:
    """2026-09-21 — 마침표 뒤에 오는 "- " 불릿은 앞선 마침표 경계가 공백을 먼저 소비해
    대시가 다음 문장 맨 앞에 남았다("- 다만, …"). 실측 210만 조각."""

    def test_bullet_after_period_is_stripped(self) -> None:
        section = _section("첫 번째 문장입니다.\n- 다만 확정된 사항은 없습니다.")
        assert extract_phrase_candidates([section]) == [
            "첫 번째 문장입니다.",
            "다만 확정된 사항은 없습니다.",
        ]

    def test_signed_number_without_space_is_kept(self) -> None:
        section = _section("-120억원 규모의 손실이 발생하였습니다.")
        assert extract_phrase_candidates([section]) == ["-120억원 규모의 손실이 발생하였습니다."]


class TestFormFieldBoundary:
    """2026-09-21 "여섯 번째 보완" — 조회공시 답변·해명공시 서식은 항목을 마침표 없이
    줄바꿈으로만 끊어서, PRD가 겨냥한 상투구가 기사 헤드라인과 한 조각으로 붙었다
    (실측: 목표 섹션 199개의 "검토…확정된 사항" 문장 48개 중 9개 오염)."""

    def test_newline_separates_form_fields(self) -> None:
        section = _section(
            "1. 제목: 4조 규모 지분매각 추진 보도에 관한 조회공시 요구에 대한 답변\n"
            "2. 내용: 당사는 다양한 방안을 검토하고 있으나, 현재까지 확정된 사항은 없습니다."
        )
        candidates = extract_phrase_candidates([section])
        cliche = "당사는 다양한 방안을 검토하고 있으나, 현재까지 확정된 사항은 없습니다."
        assert cliche in candidates

    def test_field_label_prefix_is_stripped(self) -> None:
        section = _section("풍문 또는 보도의 내용에 대한 해명내용\n해명: 확정된 사항은 없습니다.")
        candidates = extract_phrase_candidates([section])
        assert "확정된 사항은 없습니다." in candidates

    def test_label_like_text_inside_sentence_is_untouched(self) -> None:
        """콜론 앞이 "-다"로 끝나는 서술문이면 라벨이 아니다 — 앞부분을 잘라내면
        안 된다(R104-2 수정 이후: 길이가 아니라 "-다" 종결 여부로 판별한다)."""
        section = _section("당사는 다음과 같은 사항을 검토 중입니다: 신규 사업 진출을 검토합니다.")
        candidates = extract_phrase_candidates([section])
        assert any(c.startswith("당사는 다음과 같은") for c in candidates)

    def test_numeric_label_is_not_treated_as_field_label(self) -> None:
        """ "주1: …"처럼 숫자가 섞인 표 주석 라벨은 애초에 콜론 앞 패턴(한글로만
        시작)과 안 맞아 라벨 판정 대상이 아니다."""
        section = _section("주1: 상기 금액은 확정된 사항이 아닙니다.")
        candidates = extract_phrase_candidates([section])
        assert candidates == ["주1: 상기 금액은 확정된 사항이 아닙니다."]

    def test_denial_prefix_under_length_limit_is_not_treated_as_field_label(self) -> None:
        """R104-2 (2026-09-21 외부 리뷰, 독립 재현으로 확인) — 콜론 앞이 "다음은
        사실이 아닙니다"처럼 "-다"로 끝나는 서술·부정문이면 서식 라벨이 아니라고
        보고 보존해야 한다. 처음엔 이걸 화이트리스트로 고쳤는데, 화이트리스트 조사
        범위가 TARGET_SECTION_PATTERN 대상 섹션에 한정돼 있어 그 밖의 실제 라벨
        수백 개를 못 걷어내는 새 문제가 드러났다(독립 code-reviewer 재검토 지적) —
        그래서 "-다"로 끝나는지(명사구 라벨은 구조적으로 이걸로 안 끝남, 실측:
        코퍼스 전체 고유 라벨 후보 477개 중 0개) 판별하는 방식으로 다시 바꿨다."""
        section = _section("다음은 사실이 아닙니다: 당사는 신규 사업 진출을 확정하였습니다.")
        candidates = extract_phrase_candidates([section])
        assert candidates == ["다음은 사실이 아닙니다: 당사는 신규 사업 진출을 확정하였습니다."]

    def test_real_field_label_outside_target_sections_is_still_stripped(self) -> None:
        """독립 code-reviewer 재검토 지적 반영 — TARGET_SECTION_PATTERN 밖(예:
        정기공시·주요사항보고)의 실제 서식 라벨도 걷어내야 한다. "정정사유"(정정
        보고서에서 흔한 라벨, 옛 화이트리스트엔 없었던 항목)로 확인한다."""
        section = _section(
            "정정사유: 코스닥시장 상장규정 개정에 따른 자진 정정 신고서 제출입니다.\n"
            "관련 세부 절차는 추후 공지드리겠습니다."
        )
        candidates = extract_phrase_candidates([section])
        assert not any(c.startswith("정정사유") for c in candidates)
        assert "코스닥시장 상장규정 개정에 따른 자진 정정 신고서 제출입니다." in candidates

    def test_long_field_label_beyond_old_twelve_char_cap_is_stripped(self) -> None:
        """실코퍼스 최장 라벨 실측(25자, "정기주주총회 소집을 위한 권리주주 확정
        기준일")에 맞춰 길이 상한을 30자로 넓혔다 — 예전 12자 상한이면 이런 긴
        라벨은 아예 라벨로 인식조차 못 했다."""
        section = _section("기타 투자판단과 관련한 중요사항: 특별한 사항이 없어 종료 처리합니다.")
        candidates = extract_phrase_candidates([section])
        assert candidates == ["특별한 사항이 없어 종료 처리합니다."]


class TestAccountingNoteFilter:
    """2026-09-21 "여섯 번째 보완" — A11(scripts/extract_cliche_candidates.py)의 K-IFRS
    어휘 필터가 프로덕션 경로엔 연결돼 있지 않아, 통과 클러스터 멤버 문구의 24.2%가
    회계기준 주석이었고 근접 클러스터 1,250쌍 대부분이 그 과분할이었다."""

    def test_strong_marker_sentence_is_filtered(self) -> None:
        section = _section("해당 기준서의 개정이 재무제표에 미치는 중요한 영향은 없습니다.")
        assert extract_phrase_candidates([section]) == []

    def test_two_weak_vocab_terms_filter_sentence(self) -> None:
        section = _section("금융자산의 공정가치 변동은 당기손익으로 인식합니다.")
        assert extract_phrase_candidates([section]) == []

    def test_single_weak_vocab_term_is_kept(self) -> None:
        """문맥에 따라 일반 서술에도 나오는 어휘는 1회 등장만으로 거르지 않는다."""
        section = _section("당사는 공정가치 평가를 검토하고 있으나 확정된 사항은 없습니다.")
        assert extract_phrase_candidates([section]) == [
            "당사는 공정가치 평가를 검토하고 있으나 확정된 사항은 없습니다."
        ]

    def test_target_cliche_survives_filter(self) -> None:
        """PRD가 겨냥한 IR 상투구는 회계 어휘가 없으므로 그대로 남아야 한다(회귀 감시)."""
        section = _section("당사는 다양한 방안을 검토하고 있으나, 현재까지 확정된 사항은 없습니다.")
        assert extract_phrase_candidates([section]) == [
            "당사는 다양한 방안을 검토하고 있으나, 현재까지 확정된 사항은 없습니다."
        ]

    def test_section_title_brackets_are_filtered(self) -> None:
        section = _section("【 대표이사 등의 확인 】 상기 내용을 확인하였습니다.")
        assert extract_phrase_candidates([section]) == []

    def test_strong_marker_embedded_in_unrelated_compound_is_not_reproduced_in_real_corpus(
        self,
    ) -> None:
        """2026-09-22 팀 리뷰(@9abin) 지적 — `_is_accounting_note()`의 substring
        매칭이 "평가기준서식"처럼 무관한 합성어 안의 "기준서"까지 잡을 수 있다는
        우려. 실측(114개사 코퍼스)으로 검토한 결과: 강한 마커 8종 전체 376,268건
        매칭 중 81.8%(307,840건)가 앞/뒤에 한글이 붙은 형태다(조사가 공백 없이
        붙는 한국어 특성상 "기준서의"/"기준서를" 같은 정상 용례가 대부분) — 단어
        경계 체크를 넣으면 진짜 회계주석의 82%를 걸러내지 못하게 돼, 지적된
        시나리오보다 훨씬 큰 손해다. 그래서 수정은 보류했다(`_is_accounting_note()`
        docstring 참고) — 이 테스트는 그 판단의 근거였던 정상 용례(조사가 바로
        붙는 경우)가 여전히 올바르게 회계주석으로 걸러지는지만 회귀 감시한다."""
        section = _section("해당 기준서의 개정 내용은 다음과 같습니다.")
        assert extract_phrase_candidates([section]) == []


class TestSelectClusteringPool:
    """2026-09-21 후보 풀 재설계 — 골드셋 근거 공시(조회공시·해명공시)의 상투구가
    회사별 고유 내용과 한 문장에 섞여 "5개사 이상 정확 일치" 필터에서 전부 탈락하던
    문제(83개 중 76개가 1개사). 목표 섹션의 문장은 사전 필터 없이 풀에 넣고, 나머지는
    느슨한 문자열 필터만 건다. 회사 수 본판정은 클러스터링 뒤(`PhraseClusterIndex.
    build(min_companies=...)`)."""

    def test_pool_keeps_cross_company_and_target_section_sentences(self) -> None:
        shared = "이 문장은 두 회사에 똑같이 등장합니다."
        unique = "이 문장은 한 회사에만 등장합니다."
        targeted = "현재까지 구체적으로 확정된 사항은 없습니다."
        sections = [
            _section(shared, corp_code="C1"),
            _section(shared, corp_code="C2"),
            _section(unique, corp_code="C3"),
            DisclosureSection(
                rcept_no="R9",
                corp_code="C4",
                form_type="기타",
                section_title="풍문 또는 보도에 대한 해명",
                text=targeted,
            ),
        ]

        pool, companies = select_clustering_pool(sections)

        assert LEXICAL_MIN_COMPANIES == 2
        assert pool == [shared, targeted]  # 첫 등장 순, unique는 1개사라 탈락
        assert companies[shared] == {"C1", "C2"}
        assert companies[unique] == {"C3"}  # 풀 밖 문구의 회사 집합도 돌려준다
        assert companies[targeted] == {"C4"}

    def test_near_universal_phrase_is_excluded(self) -> None:
        """2026-09-21 "여섯 번째 보완" — 법정 의무 문구는 회사가 선택하는 게 아니라
        거의 전부가 쓴다(실측: 풀 상위가 전부 회계기준 주석이었고, 그중엔 K-IFRS
        어휘가 아예 없어 어휘 필터로는 못 잡는 것도 많았다)."""
        universal = "추정 및 가정은 지속적으로 평가되고 있습니다."
        selective = "당사는 다양한 방안을 검토 중이나 확정된 사항은 없습니다."
        sections = [
            _section(universal + " " + (selective if c in ("C1", "C2") else ""), corp_code=c)
            for c in ("C1", "C2", "C3", "C4", "C5", "C6")
        ]

        pool, companies = select_clustering_pool(sections)

        assert len(companies[universal]) == 6  # 6개사 전부 = 100% > MAX_COMPANY_FRACTION
        assert universal not in pool
        assert selective in pool  # 2/6 = 33%, 하한(2) 바닥이 깔려 통과

    def test_target_section_phrase_is_exempt_from_fraction_cap(self) -> None:
        """널리 쓰이는 IR 화법이야말로 제품이 찾는 것이라, 목표 섹션 문장에는 회사 수
        상한을 걸지 않는다 — 상한은 목표 섹션 밖 법정 의무 문구를 걷어내는 용도다."""
        widespread_cliche = "현재까지 구체적으로 확정된 사항은 없습니다."
        sections = [
            DisclosureSection(
                rcept_no=f"R{i}",
                corp_code=f"C{i}",
                form_type="기타",
                section_title="풍문 또는 보도에 대한 해명",
                text=widespread_cliche,
            )
            for i in range(6)
        ]

        pool, companies = select_clustering_pool(sections)

        assert len(companies[widespread_cliche]) == 6  # 전 회사가 쓰지만
        assert widespread_cliche in pool  # 목표 섹션 출신이라 상한 면제

    def test_fraction_cap_never_falls_below_lexical_floor(self) -> None:
        """회사가 몇 개 없는 코퍼스(드라이런·단위 테스트)에서 비율 상한이 하한보다
        낮아지면 풀이 통째로 비어버린다 — 상한에 바닥을 깔아 그걸 막는다."""
        shared = "이 문장은 두 회사에 똑같이 등장합니다."
        sections = [_section(shared, corp_code="C1"), _section(shared, corp_code="C2")]

        pool, _ = select_clustering_pool(sections)

        assert pool == [shared]  # 2개사 * 0.5 = 1.0 이지만 하한(2)이 바닥을 깐다

    def test_target_section_is_detected_from_text_head_too(self) -> None:
        targeted = "당사는 다양한 방안을 검토 중이나 결정된 사항은 없습니다."
        header = "본 공시는 풍문 또는 보도에 대한 해명(미확정) 공시입니다."
        section = _section(header + "\n" + targeted, corp_code="C1")  # 제목엔 단서 없음

        pool, _ = select_clustering_pool([section])

        assert targeted in pool


class TestExtractPhraseCandidates:
    def test_splits_on_sentence_boundary(self) -> None:
        section = _section("첫 번째 문장입니다. 두 번째 문장입니다.")
        assert extract_phrase_candidates([section]) == [
            "첫 번째 문장입니다.",
            "두 번째 문장입니다.",
        ]

    def test_filters_short_fragments(self) -> None:
        section = _section("짧음. 이건 충분히 긴 문장입니다.")
        candidates = extract_phrase_candidates([section])
        assert "짧음." not in candidates
        assert len("짧음.") < MIN_PHRASE_LENGTH

    def test_filters_extremely_long_fragment(self) -> None:
        """실측(PR #57, A) — match_many() 배치 매칭을 실 코퍼스로 돌리다 CUDA OOM
        재현. 마침표가 거의 없는 텍스트(구조를 못 찾은 "본문(미분할)" 폴백 섹션 전체
        등)가 통째로 "문장 하나"가 되면서 실제로 32,450자짜리 후보가 나왔다 — 이런
        항목이 배치에 섞이면 그 배치 전체가 그 길이로 패딩되며 어텐션 메모리가 터진다.
        서술형 종결어미가 있고 숫자 비중이 낮아도(=_is_narrative_sentence는 통과) 너무
        길면 걸러야 한다."""
        too_long = "가" * (MAX_PHRASE_LENGTH + 1) + "습니다."
        assert len(too_long) > MAX_PHRASE_LENGTH
        section = _section(f"{too_long} 이건 충분히 길지만 정상 범위인 서술형 문장입니다.")

        candidates = extract_phrase_candidates([section])

        assert too_long not in candidates
        assert "이건 충분히 길지만 정상 범위인 서술형 문장입니다." in candidates

    def test_deduplicates_across_sections(self) -> None:
        repeated = "다양한 방안을 검토하고 있으나 확정된 사항은 없습니다."
        sections = [_section(repeated, rcept_no="R1"), _section(repeated, rcept_no="R2")]
        assert extract_phrase_candidates(sections) == [repeated]

    def test_empty_sections_returns_empty(self) -> None:
        assert extract_phrase_candidates([]) == []

    def test_filters_table_header_without_narrative_ending(self) -> None:
        """소유보고서 표 헤더는 서술형 종결어미로 끝나지 않아 걸러진다."""
        section = _section(
            "보고자에 관한 사항 보고구분 변동 보고자 구분 개인 성명 한 글 김상범. "
            "이건 충분히 긴 서술형 문장입니다."
        )
        candidates = extract_phrase_candidates([section])
        assert candidates == ["이건 충분히 긴 서술형 문장입니다."]

    def test_filters_digit_heavy_fragment_even_with_narrative_ending(self) -> None:
        """숫자 비중이 너무 높으면 종결어미가 우연히 맞아도 걸러진다(재무제표 등)."""
        section = _section(
            "12345678901234567890123456789012345678901234567890 자금을 조달함. "
            "이건 충분히 긴 서술형 문장입니다."
        )
        candidates = extract_phrase_candidates([section])
        assert candidates == ["이건 충분히 긴 서술형 문장입니다."]

    def test_min_companies_defaults_to_no_filtering(self) -> None:
        """기본값(1)은 사실상 필터 없음 — _build_outcome_labels()가 섹션 하나씩
        넘기며 매칭하는 호출부라, 회사 빈도 문턱이 걸리면 안 된다."""
        section = _section("선제적 재무구조 개선을 추진하고 있습니다.", corp_code="C1")
        assert extract_phrase_candidates([section]) == ["선제적 재무구조 개선을 추진하고 있습니다."]

    def test_min_companies_filters_out_single_company_phrase(self) -> None:
        """실측(PR #57, A11과 같은 원칙): 한 회사만 쓰는 문장은 그 회사 특유의 문체일
        뿐 여러 회사가 공유하는 상투구가 아니다."""
        phrase = "선제적 재무구조 개선을 추진하고 있습니다."
        sections = [_section(phrase, rcept_no="R1", corp_code="C1")]

        assert extract_phrase_candidates(sections, min_companies=2) == []

    def test_min_companies_keeps_phrase_shared_across_enough_companies(self) -> None:
        phrase = "선제적 재무구조 개선을 추진하고 있습니다."
        sections = [
            _section(phrase, rcept_no="R1", corp_code="C1"),
            _section(phrase, rcept_no="R2", corp_code="C2"),
        ]

        assert extract_phrase_candidates(sections, min_companies=2) == [phrase]

    def test_min_companies_counts_distinct_companies_not_occurrences(self) -> None:
        """같은 회사가 반복해서 쓰는 건 "여러 회사가 공유하는 화법"으로 안 잡힌다."""
        phrase = "선제적 재무구조 개선을 추진하고 있습니다."
        sections = [
            _section(phrase, rcept_no="R1", corp_code="C1"),
            _section(phrase, rcept_no="R2", corp_code="C1"),  # 같은 회사, 다른 공시
        ]

        assert extract_phrase_candidates(sections, min_companies=2) == []


class TestListLabelHandling:
    """실측(2026-09-18, 사용자 제보 — 삼성전자 공정거래법 제26조 대규모내부거래
    공시)에서 확인된 표 양식 공시 케이스. "가./나./1." 같은 항목 번호 라벨의
    마침표가 문장 경계로 오인되던 문제를 고친다."""

    def test_item_labels_do_not_create_false_sentence_from_neighboring_content(self) -> None:
        """실측 원문 축약 재현 — 예전엔 "나."와 "다." 라벨 사이 내용이 우연히
        서술형 종결어미 검사를 통과해 "출자목적물 보통주식 다." 하나만 후보로
        잘못 나왔었다. 라벨을 먼저 제거하면 이 가짜 후보가 더 이상 안 나와야 한다."""
        section = _section(
            "2. 출자내역 가. 출자일자 2026년 5월부터 2027년 1월까지의 기간 내에 "
            "총 2차에 걸쳐 나누어 출자 예정 나. 출자목적물 보통주식 다. 출자금액 "
            "120 라. 출자상대방 총출자액 4,000"
        )
        candidates = extract_phrase_candidates([section])
        assert "출자목적물 보통주식 다." not in candidates

    def test_single_syllable_label_stripped_but_real_word_kept(self) -> None:
        """라벨 제거는 "공백/문두 + 한글 한 글자 + 마침표 + 공백" 패턴에만 좁게
        적용된다 — "짧음." 처럼 두 글자 이상인 실제 단어는 건드리지 않는다."""
        section = _section("짧음. 이건 충분히 긴 서술형 문장입니다.")
        candidates = extract_phrase_candidates([section])
        assert "이건 충분히 긴 서술형 문장입니다." in candidates

    def test_numbered_section_header_label_itself_is_stripped(self) -> None:
        """ "5." 라벨 자체(마침표 포함)는 후보 텍스트에서 사라져야 한다 — 라벨
        뒤의 실제 내용("기타 이 회사는...")까지 지울 필요는 없다."""
        section = _section("5. 기타 이 회사는 정관 개정을 검토하고 있습니다.")
        candidates = extract_phrase_candidates([section])
        assert len(candidates) == 1
        assert not candidates[0].startswith("5.")
        assert "이 회사는 정관 개정을 검토하고 있습니다." in candidates[0]


class TestAccountingSubnoteLabel:
    """2026-09-22 상세 감사(review-agent) + 팀 리뷰(@9abin) — K-IFRS 회계주석의
    "2-6"/"9-2" 같은 하위번호 소제목이 마침표 뒤에 공백 없이 붙는 서식을 처리하다,
    "아무 공백 뒤"까지 너무 넓게 잡아 일반 문장의 숫자 범위까지 지워버린 사고가
    났다(팀 리뷰 지적, 독립 재현). 개행 뒤로 조건을 좁혀 재수정했다."""

    def test_ordinary_numeric_range_in_a_sentence_is_preserved(self) -> None:
        """회귀 테스트(팀 리뷰 @9abin 지적 반례) — "2-3분기"는 회계주석 라벨이
        아니라 문장 중간의 평범한 숫자 범위다. 이전 버전(공백 뒤 전부 매칭)은
        "2-3"을 통째로 지워 "당사는 향후  분기 내..."처럼 문장을 손상시켰다."""
        section = _section("당사는 향후 2-3분기 내 실적을 발표할 예정입니다.")
        candidates = extract_phrase_candidates([section])
        assert candidates == ["당사는 향후 2-3분기 내 실적을 발표할 예정입니다."]

    def test_road_address_lot_number_is_preserved(self) -> None:
        """실측(114개사 코퍼스)에서 "공백 뒤 숫자-숫자" 오탐의 대다수(10,519건 중
        다수)가 도로명주소 번지수였다 — 이런 패턴이 보존되는지 회귀 감시한다."""
        section = _section("본사 소재지는 서울 서초구 강남대로 39길 15-10입니다.")
        candidates = extract_phrase_candidates([section])
        assert candidates == ["본사 소재지는 서울 서초구 강남대로 39길 15-10입니다."]

    def test_decimal_point_followed_by_range_is_preserved(self) -> None:
        """회귀 테스트(팀 리뷰 @9abin 재검토 반례) — "3.5-4.0%"처럼 소수점 뒤에
        숫자 구간이 오면, 그 앞의 마침표는 "마침표 직후 진짜 라벨"과 똑같은
        `(?<=\\.)` 패턴이라 예전엔 "5-4"까지 라벨로 오인돼 지워졌다("3. .0%"로
        손상). 마침표 앞이 숫자면(소수점) 문장 종결 마침표가 아니라고 보고
        보존해야 한다."""
        section = _section("당사의 예상 성장률은 3.5-4.0%로 전망됩니다.")
        candidates = extract_phrase_candidates([section])
        assert candidates == ["당사의 예상 성장률은 3.5-4.0%로 전망됩니다."]

    def test_subnote_label_right_after_period_is_still_stripped(self) -> None:
        """원래 고치려던 버그(마침표 직후, 공백 없이 붙는 진짜 회계주석 라벨)는
        계속 고쳐져야 한다 — 좁힌 조건이 원래 수정 효과까지 되돌리면 안 된다."""
        section = _section(
            "당사는 대가의 선지급이나 선수취로 인한 거래일을 각각 결정합니다."
            "2-6 현금및현금성자산재무상태표상의 현금및현금성자산은 보통예금과 "
            "소액현금으로 구성되어 있습니다."
        )
        candidates = extract_phrase_candidates([section])
        assert "당사는 대가의 선지급이나 선수취로 인한 거래일을 각각 결정합니다." in candidates
        assert not any(c.startswith("2-6") for c in candidates)

    def test_subnote_label_right_after_newline_is_still_stripped(self) -> None:
        """줄바꿈 뒤에 오는 라벨(다른 경계로 이미 분리된 문장의 맨 앞)도 여전히
        제거돼야 한다."""
        section = _section(
            "1. 제목: 재무제표 주석\n"
            "2-2 연결기준연결재무제표는 당기말 현재 연결기업의 재무제표로 구성되어 "
            "있습니다."
        )
        candidates = extract_phrase_candidates([section])
        assert any(c.startswith("연결기준연결재무제표는") for c in candidates)


class TestBulletBoundary:
    """ "- " 불릿으로만 구분되고 마침표가 없는 서술형 메모(실측 — "5. 기타" 섹션
    스타일)도 개별 후보로 뽑혀야 한다."""

    def test_dash_separated_narrative_notes_are_split_into_candidates(self) -> None:
        section = _section(
            "- 국가 AI컴퓨팅 센터는 신설예정 법인이므로 이사회 의결 및 공시를 "
            "진행함 - 120억원을 총 2차에 걸쳐 나누어 출자할 예정임"
        )
        candidates = extract_phrase_candidates([section])
        assert "국가 AI컴퓨팅 센터는 신설예정 법인이므로 이사회 의결 및 공시를 진행함" in candidates
        assert "120억원을 총 2차에 걸쳐 나누어 출자할 예정임" in candidates

    def test_dash_separated_non_narrative_fragment_still_filtered(self) -> None:
        """회귀 확인 — 불릿 경계를 추가해도, 길이는 충분하지만 서술형 종결어미로
        안 끝나는 표 조각(예: 담당 이사회 구성 명단)은 여전히 걸러져야 한다 —
        마침표 완화(`\\.?`)가 이런 조각까지 통과시키면 안 된다."""
        section = _section(
            "- 사외이사 참석 여부는 다음과 같은 명단으로 구성 - 이건 충분히 긴 서술형 문장입니다."
        )
        candidates = extract_phrase_candidates([section])
        assert candidates == ["이건 충분히 긴 서술형 문장입니다."]


class TestLoadSectionsJsonl:
    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        assert load_sections_jsonl(tmp_path / "없음.jsonl") == []

    def test_roundtrips_disclosure_section(self, tmp_path: Path) -> None:
        import dataclasses

        section = _section("본문 내용입니다.")
        path = tmp_path / "sections.jsonl"
        path.write_text(
            json.dumps(dataclasses.asdict(section), ensure_ascii=False) + "\n", encoding="utf-8"
        )

        loaded = load_sections_jsonl(path)

        assert loaded == [section]

    def test_skips_blank_lines(self, tmp_path: Path) -> None:
        path = tmp_path / "sections.jsonl"
        path.write_text("\n\n", encoding="utf-8")
        assert load_sections_jsonl(path) == []


class TestLoadAllLocalSections:
    def test_missing_raw_dir_returns_empty(self, tmp_path: Path) -> None:
        assert load_all_local_sections(tmp_path / "없음") == []

    def test_aggregates_multiple_companies(self, tmp_path: Path) -> None:
        import dataclasses

        for corp_code in ("C1", "C2"):
            corp_dir = tmp_path / corp_code
            corp_dir.mkdir()
            section = _section("회사별 섹션입니다.", rcept_no=f"R-{corp_code}")
            (corp_dir / "sections.jsonl").write_text(
                json.dumps(dataclasses.asdict(section), ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

        loaded = load_all_local_sections(tmp_path)

        assert {s.rcept_no for s in loaded} == {"R-C1", "R-C2"}
