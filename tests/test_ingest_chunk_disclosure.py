"""A6 — chunk_disclosure 단위 테스트.

실제 시장 데이터로 확인된 두 원문 포맷(Family A: dart4.xsd 구조화 XML, Family B: HTML)을
합성 픽스처로 재현해서 검증한다. 실제 공시 원문 텍스트를 그대로 쓰지 않고 구조만 본떴다.
"""

from __future__ import annotations

from disclosure_lit.dart.ingest import (
    FALLBACK_FORM_TYPE,
    FALLBACK_SECTION_TITLE,
    _render_table_row,
    _render_table_rows,
    _strip_signature_block,
    chunk_disclosure,
)

# --------------------------------------------------------------------- Family A (구조화 XML)

STRUCTURED_XML = """<?xml version="1.0" encoding="utf-8"?>
<DOCUMENT>
<DOCUMENT-NAME ACODE="11306">주요사항보고서(유상증자결정)</DOCUMENT-NAME>
<COMPANY-NAME>테스트 주식회사</COMPANY-NAME>
<BODY>
<SECTION-1 ACLASS="MANDATORY">
<TITLE ATOC="Y">거래소 신고의무 사항</TITLE>
<P>금융위원회 / 한국거래소 귀중</P>
</SECTION-1>
<SECTION-1 ACLASS="MANDATORY">
<TITLE ATOC="Y">유상증자 결정</TITLE>
<TABLE-GROUP>
<TABLE><TR><TD>신주의 종류</TD><TD>보통주식</TD></TR></TABLE>
</TABLE-GROUP>
<P>발행가액은 R&D 투자 계획을 반영해 산정되었다.</P>
</SECTION-1>
</BODY>
</DOCUMENT>
"""

# TITLE 이 없는 SECTION-1 하나만 있는 경우 (임원ㆍ주요주주 지분보고서 계열에서 실제 관측됨)
STRUCTURED_XML_NO_TITLE = """<?xml version="1.0" encoding="utf-8"?>
<DOCUMENT>
<DOCUMENT-NAME>임원ㆍ주요주주 특정증권등 소유상황보고서</DOCUMENT-NAME>
<BODY>
<LIBRARY>
<SECTION-1 ACLASS="MANDATORY">
<P>보고자: 홍길동</P>
</SECTION-1>
</LIBRARY>
</BODY>
</DOCUMENT>
"""

STRUCTURED_XML_NO_SECTION = """<?xml version="1.0" encoding="utf-8"?>
<DOCUMENT>
<DOCUMENT-NAME>알 수 없는 서식</DOCUMENT-NAME>
<BODY><P>섹션 구조가 없는 원문</P></BODY>
</DOCUMENT>
"""


class TestStructuredXml:
    def test_splits_by_section1_and_uses_title(self) -> None:
        sections = chunk_disclosure(STRUCTURED_XML.encode("utf-8"), rcept_no="R1", corp_code="C1")

        assert len(sections) == 2
        assert sections[0].section_title == "거래소 신고의무 사항"
        assert sections[1].section_title == "유상증자 결정"
        assert all(s.form_type == "유상증자" for s in sections)
        assert all(s.rcept_no == "R1" and s.corp_code == "C1" for s in sections)

    def test_fixes_unescaped_ampersand(self) -> None:
        """본문에 이스케이프 안 된 '&'가 있어도(실측 확인된 실제 현상) 파싱이 깨지지 않는다."""
        sections = chunk_disclosure(STRUCTURED_XML.encode("utf-8"), rcept_no="R1", corp_code="C1")
        assert "R&D" in sections[1].text

    def test_missing_title_falls_back_but_still_splits(self) -> None:
        sections = chunk_disclosure(
            STRUCTURED_XML_NO_TITLE.encode("utf-8"), rcept_no="R2", corp_code="C2"
        )
        assert len(sections) == 1
        assert sections[0].section_title == FALLBACK_SECTION_TITLE
        assert "홍길동" in sections[0].text

    def test_no_section1_falls_back_to_whole_document(self) -> None:
        sections = chunk_disclosure(
            STRUCTURED_XML_NO_SECTION.encode("utf-8"), rcept_no="R3", corp_code="C3"
        )
        assert len(sections) == 1
        assert "(미분할)" in sections[0].section_title
        assert "섹션 구조가 없는 원문" in sections[0].text

    def test_lxml_recovers_structure_from_badly_truncated_xml(self) -> None:
        """lxml recover 모드는 잘못된 바이트 시퀀스가 섞인 중간에 끊긴 XML에서도
        SECTION-1/TITLE 을 최대한 복구해낸다 — 표준 xml.etree 라면 그냥 예외로 죽었을
        입력이다. 크래시 없이 처리된다는 것만 확인한다(복구된 내용의 정확성까지는
        보장 못 함)."""
        broken = b"<DOCUMENT><BODY><SECTION-1><TITLE>\xff\xfe broken"
        sections = chunk_disclosure(broken, rcept_no="R4", corp_code="C4")
        assert len(sections) == 1  # 크래시 없이 뭔가는 반환한다

    def test_empty_content_falls_back_without_crashing(self) -> None:
        """recover=True 로도 못 넘어가는 극단적인 경우(빈 내용, 순수 텍스트)는
        lxml 이 XMLSyntaxError 를 던지거나 root=None 을 반환한다 — 둘 다 크래시 없이
        "본문(미분할)" 폴백으로 처리돼야 한다."""
        for broken in (b"", b"   ", b"this is not xml at all"):
            sections = chunk_disclosure(broken, rcept_no="R4", corp_code="C4")
            assert len(sections) == 1
            assert "(미분할)" in sections[0].section_title


# --------------------------------------------------------------------- Family B (HTML)

HTML_SINGLE_TABLE = """<html><head><title>테스트회사/단일판매공급계약/공시</title></head>
<body>
<div class="xforms_title"><div><span>단일판매공급계약체결(자율공시)</span></div></div>
<table><tbody>
<tr><td>1. 계약 구분</td><td>상품공급</td></tr>
<tr><td>2. 계약금액(원)</td><td>1,000,000</td></tr>
</tbody></table>
</body></html>
"""

# 실제 관측된 "한 문서 안에 번호 체계가 두 번(각각 1부터) 나오는" 케이스를 재현 —
# 본문 표와 "세부내역" 표가 독립적으로 1,2,3...을 쓴다.
HTML_MULTI_TABLE_DUPLICATE_NUMBERING = """<html><head><title>테스트회사/임시총회/공시</title></head>
<body>
<div class="xforms_title"><div><span>임시주주총회 결과</span></div></div>
<table><tbody>
<tr><td>1. 의결권행사기준일</td><td>2026-08-07</td></tr>
<tr><td>2. 주주총회 일자</td><td>2026-09-10</td></tr>
</tbody></table>
<p>【부동산개발사업 투자비율 변경내역】</p>
<table><tbody>
<tr><td>1. 신규상장시 해당여부</td><td>예</td></tr>
<tr><td>2. 투자비율 변경</td><td>아니오</td></tr>
</tbody></table>
</body></html>
"""

HTML_TABLE_WITHOUT_ANY_TITLE_HINT = """<html><head><title></title></head>
<body>
<table><tbody><tr><td>1. 항목</td><td>값</td></tr></tbody></table>
<table><tbody><tr><td>2. 다른 항목</td><td>값2</td></tr></tbody></table>
</body></html>
"""

HTML_NO_TABLE_AT_ALL = """<html><head><title>공시</title></head>
<body><div>표가 전혀 없는 자유 서술형 본문입니다.</div></body></html>
"""

# <td>/<th> 없이 <tr> 안에 텍스트가 바로 들어간 malformed 케이스(HWP 변환 산출물 등) —
# 조용히 버리지 않고 셀 하나로 얹어야 한다("조용히 정보를 버리지 않는다" 원칙, PR #34).
HTML_TABLE_TEXT_WITHOUT_CELL_WRAPPER = """<html><head><title>공시</title></head>
<body>
<table><tbody>
<tr>셀 태그 없는 텍스트</tr>
</tbody></table>
</body></html>
"""

# HWP->HTML 변환 산출물에 흔한, 레이아웃용으로 <table> 안에 <table>이 또 들어있는 경우
# (리뷰 지적, PR #34, @powercodingman). 바깥 표 내용("바깥표시작"/"바깥표끝")이
# 안쪽 표 때문에 유실되면 안 된다.
HTML_NESTED_TABLE = """<html><head><title>공시</title></head>
<body>
<table><tbody>
<tr><td>바깥표시작</td></tr>
<tr><td><table><tbody><tr><td>안쪽표내용</td></tr></tbody></table></td></tr>
<tr><td>바깥표끝</td></tr>
</tbody></table>
</body></html>
"""


# 실제 관측된 헤더 + 여러 데이터 행 matrix 표(주주총회 안건 세부내역류) — 열이 3개 이상이라
# 2열 라벨/값 표와 달리 행이 뭉개지면 어떤 값이 어떤 행·열에 속하는지 알 수 없게 된다.
HTML_MATRIX_TABLE = """<html><head><title>테스트회사/주주총회결과/공시</title></head>
<body>
<div class="xforms_title"><div><span>임시주주총회 결과</span></div></div>
<table><tbody>
<tr><th>안건번호</th><th>안건내용</th><th>가결여부</th></tr>
<tr><td>1</td><td>이사 선임의 건</td><td>가결</td></tr>
<tr><td>2</td><td>감사 선임의 건</td><td>부결</td></tr>
</tbody></table>
</body></html>
"""


class TestRenderTableRow:
    """_render_table_row/_render_table_rows 단위 테스트 — chunk_disclosure 를 거치지 않고
    렌더링 규칙만 직접 검증한다."""

    def test_two_cell_row_renders_as_label_colon_value(self) -> None:
        assert _render_table_row(["계약금액", "1,000,000"]) == "계약금액: 1,000,000"

    def test_row_with_other_cell_counts_joins_with_pipe(self) -> None:
        assert _render_table_row(["안건번호", "안건내용", "가결여부"]) == (
            "안건번호 | 안건내용 | 가결여부"
        )
        assert _render_table_row(["단일셀"]) == "단일셀"

    def test_rows_joined_by_newline_preserving_row_boundaries(self) -> None:
        rows = [["안건번호", "안건내용", "가결여부"], ["1", "이사 선임의 건", "가결"]]
        assert _render_table_rows(rows) == (
            "안건번호 | 안건내용 | 가결여부\n1 | 이사 선임의 건 | 가결"
        )

    def test_fully_empty_row_is_skipped(self) -> None:
        rows = [["실데이터", "값"], ["", ""]]
        assert _render_table_rows(rows) == "실데이터: 값"


class TestHtmlDocument:
    def test_single_table_becomes_one_section(self) -> None:
        sections = chunk_disclosure(
            HTML_SINGLE_TABLE.encode("utf-8"), rcept_no="R5", corp_code="C5"
        )
        assert len(sections) == 1
        assert sections[0].section_title == "단일판매공급계약체결(자율공시)"
        assert "계약금액" in sections[0].text
        assert sections[0].form_type == "공급계약_체결"

    def test_form_type_uses_xforms_title_not_head_title_breadcrumb(self) -> None:
        """리뷰 지적(PR #26, @jh3779): <head><title>은 '회사명/제목/날짜제목' 식
        브레드크럼이라 "체결" 같은 분류 결정적 키워드가 빠질 수 있다. xforms_title div
        (실제 공시명)를 report_nm 소스로 써야 한다 — HTML_SINGLE_TABLE의 <title>엔
        "체결"이 없고 xforms_title에만 있어서, 이 테스트가 회귀를 그대로 잡아낸다."""
        assert "체결" not in HTML_SINGLE_TABLE.split("<title>")[1].split("</title>")[0]

        sections = chunk_disclosure(
            HTML_SINGLE_TABLE.encode("utf-8"), rcept_no="R5", corp_code="C5"
        )
        assert sections[0].form_type != FALLBACK_FORM_TYPE
        assert sections[0].form_type == "공급계약_체결"

    def test_duplicate_numbering_across_tables_does_not_collide(self) -> None:
        """서로 다른 표가 똑같이 '1.'부터 다시 매겨도 별개 섹션으로 분리되어야 한다 —
        번호를 섹션 키로 쓰면 두 번째 표의 '1.'이 첫 번째 표의 '1.'을 덮어써 버린다."""
        sections = chunk_disclosure(
            HTML_MULTI_TABLE_DUPLICATE_NUMBERING.encode("utf-8"),
            rcept_no="R6",
            corp_code="C6",
        )

        assert len(sections) == 2
        assert sections[0].section_title == "임시주주총회 결과"
        assert "의결권행사기준일" in sections[0].text
        assert "신규상장시" not in sections[0].text  # 두 표의 내용이 섞이지 않는다

        assert sections[1].section_title == "부동산개발사업 투자비율 변경내역"
        assert "신규상장시" in sections[1].text
        assert "의결권행사기준일" not in sections[1].text

    def test_falls_back_to_numbered_table_label_without_any_title_hint(self) -> None:
        sections = chunk_disclosure(
            HTML_TABLE_WITHOUT_ANY_TITLE_HINT.encode("utf-8"), rcept_no="R7", corp_code="C7"
        )
        assert len(sections) == 2
        assert sections[0].section_title == "표 1"
        assert sections[1].section_title == "표 2"

    def test_no_table_falls_back_to_whole_document(self) -> None:
        sections = chunk_disclosure(
            HTML_NO_TABLE_AT_ALL.encode("utf-8"), rcept_no="R8", corp_code="C8"
        )
        assert len(sections) == 1
        assert "(미분할)" in sections[0].section_title
        assert "자유 서술형 본문" in sections[0].text

    def test_nested_table_keeps_outer_content_and_forms_one_section(self) -> None:
        """중첩 <table>이 있어도 바깥 표 하나로 합쳐지고, 바깥/안쪽 내용 둘 다 보존된다
        (리뷰 지적, PR #34, @powercodingman — 예전엔 바깥 표 내용이 유실됐다)."""
        sections = chunk_disclosure(
            HTML_NESTED_TABLE.encode("utf-8"), rcept_no="R11", corp_code="C11"
        )
        assert len(sections) == 1
        assert "바깥표시작" in sections[0].text
        assert "안쪽표내용" in sections[0].text
        assert "바깥표끝" in sections[0].text

    def test_matrix_table_keeps_row_and_column_association(self) -> None:
        """헤더 + 여러 데이터 행 matrix 표(주주총회 안건 세부내역류)는 옛 구현에서 행 경계가
        공백으로 뭉개져 어떤 값이 어떤 안건에 속하는지 알 수 없게 됐다(실측 확인 — 파트 A
        보고 사항). 각 데이터 행이 자기 행의 다른 셀들과 붙어 있는 하나의 연속된 조각으로
        남아야 한다."""
        sections = chunk_disclosure(
            HTML_MATRIX_TABLE.encode("utf-8"), rcept_no="R12", corp_code="C12"
        )

        assert len(sections) == 1
        text = sections[0].text
        # 헤더 행 — <th> 도 셀로 추적된다.
        assert "안건번호 | 안건내용 | 가결여부" in text
        # 각 데이터 행이 통째로 하나의 연속된 조각으로 남는다 — "가결"이 1번 안건과,
        # "부결"이 2번 안건과 붙어 있어야 한다(뒤섞이면 안 됨).
        assert "1 | 이사 선임의 건 | 가결" in text
        assert "2 | 감사 선임의 건 | 부결" in text
        # 회귀 방지: 옛 구현이라면 공백으로 뭉개져 이런 식으로 순서가 섞인 조각이 남았다.
        assert "이사 선임의 건 부결" not in text
        assert "감사 선임의 건 가결" not in text

    def test_text_without_cell_wrapper_is_not_silently_dropped(self) -> None:
        sections = chunk_disclosure(
            HTML_TABLE_TEXT_WITHOUT_CELL_WRAPPER.encode("utf-8"), rcept_no="R13", corp_code="C13"
        )

        assert len(sections) == 1
        assert "셀 태그 없는 텍스트" in sections[0].text


class TestFormatDetection:
    def test_html_prefix_routes_to_html_chunker(self) -> None:
        sections = chunk_disclosure(
            HTML_SINGLE_TABLE.encode("utf-8"), rcept_no="R9", corp_code="C9"
        )
        assert sections[0].section_title != FALLBACK_SECTION_TITLE  # HTML 경로로 갔다는 방증

    def test_xml_prefix_routes_to_structured_chunker(self) -> None:
        sections = chunk_disclosure(STRUCTURED_XML.encode("utf-8"), rcept_no="R10", corp_code="C10")
        assert len(sections) == 2  # SECTION-1 2개로 쪼개졌다는 방증(HTML 경로면 안 그럼)


class TestRceptDtPassthrough:
    """Issue #32/DEC-005 — CompanyDisclosureIndex.search() 최신순 정렬용 rcept_dt.

    기본값 ""이라 기존 호출부(위 테스트 전부)는 안 건드려도 되고, 넘기면 모든 출력
    섹션(정상/폴백 경로 전부)에 그대로 반영돼야 한다.
    """

    def test_defaults_to_empty_string(self) -> None:
        sections = chunk_disclosure(STRUCTURED_XML.encode("utf-8"), rcept_no="R1", corp_code="C1")
        assert all(s.rcept_dt == "" for s in sections)

    def test_structured_xml_sections_carry_rcept_dt(self) -> None:
        sections = chunk_disclosure(
            STRUCTURED_XML.encode("utf-8"), rcept_no="R1", corp_code="C1", rcept_dt="20260910"
        )
        assert len(sections) == 2
        assert all(s.rcept_dt == "20260910" for s in sections)

    def test_html_sections_carry_rcept_dt(self) -> None:
        sections = chunk_disclosure(
            HTML_SINGLE_TABLE.encode("utf-8"), rcept_no="R5", corp_code="C5", rcept_dt="20260910"
        )
        assert sections[0].rcept_dt == "20260910"

    def test_fallback_section_carries_rcept_dt_too(self) -> None:
        """구조를 못 찾아 폴백("미분할")되는 경로에서도 rcept_dt는 유실되면 안 된다."""
        sections = chunk_disclosure(
            STRUCTURED_XML_NO_SECTION.encode("utf-8"),
            rcept_no="R3",
            corp_code="C3",
            rcept_dt="20260910",
        )
        assert len(sections) == 1
        assert "(미분할)" in sections[0].section_title
        assert sections[0].rcept_dt == "20260910"


class TestStripSignatureBlock:
    """Issue #60 — 서식 말미 서명란/연락처 블록 제거. 실측(data/raw/*/sections.jsonl,
    "공시책임자" 포함 134개 섹션)으로 확인한 실제 패턴을 그대로 재현한다."""

    def test_removes_parenthesized_label_and_name(self) -> None:
        text = "본 사안은 영향이 없습니다. (공시책임자) 재무지원본부장 김양석 ※ 관련공시 -"

        cleaned = _strip_signature_block(text)

        assert "김양석" not in cleaned
        assert "본 사안은 영향이 없습니다." in cleaned
        assert "※ 관련공시 -" in cleaned

    def test_removes_bracketed_label_and_name(self) -> None:
        text = (
            "차후 공시할 예정입니다. - [공시책임자] 교보증권 재무지원본부장 김양석 "
            "5. 재공시예정일 2025-03-10"
        )

        cleaned = _strip_signature_block(text)

        assert "김양석" not in cleaned
        assert "차후 공시할 예정입니다. -" in cleaned
        assert "5. 재공시예정일 2025-03-10" in cleaned

    def test_removes_chained_contact_fields(self) -> None:
        """연락처 표가 청킹 과정에서 한 줄로 이어붙은 실측 케이스(라벨이 연달아 등장)."""
        text = (
            "3. 연락처 공시책임자(성명) 경영관리부장 이강현 공시책임자(전화번호) 02-840-6871 "
            "공시담당자(성명) 매니저 박진주 공시담당자(전화번호) 02-840-6871 관련부서명 자금팀 "
            "관련부서(전화번호) 02-840-6871 4. 기타 투자판단에 참고할 사항"
        )

        cleaned = _strip_signature_block(text)

        assert "이강현" not in cleaned
        assert "박진주" not in cleaned
        assert "02-840-6871" not in cleaned
        assert "3. 연락처" in cleaned
        assert "4. 기타 투자판단에 참고할 사항" in cleaned

    def test_removes_name_field_with_phone_suffix(self) -> None:
        text = (
            "공시책임자명(전화번호) 경영관리부장 이강현(02-840-6871) "
            "공시담당자명(전화번호) 매니저 박진주(02-840-6871)"
        )

        cleaned = _strip_signature_block(text)

        assert "이강현" not in cleaned
        assert "박진주" not in cleaned

    def test_preserves_narrative_sentence_using_bare_label(self) -> None:
        """본문 서술에 등장하는 "공시책임자"는 괄호가 안 붙어 있으므로 그대로 둔다."""
        text = "당사는 관련 규정에 따라 공시책임자 1명, 공시담당자 2명을 지정하고 있습니다."

        cleaned = _strip_signature_block(text)

        assert cleaned == text

    def test_preserves_sentence_with_particle_after_label(self) -> None:
        text = "공시서류를 작성하여 공시책임자에게 보고하며 공시책임자는 이를 검토합니다."

        cleaned = _strip_signature_block(text)

        assert cleaned == text

    def test_preserves_compound_noun_using_label(self) -> None:
        text = "2024년 04월 11일 공시책임자 지정에 따른 공시책임자전문과정 이수"

        cleaned = _strip_signature_block(text)

        assert cleaned == text

    def test_text_without_signature_label_is_unchanged(self) -> None:
        text = "당사는 유상증자를 통해 500억원을 조달할 예정입니다."

        assert _strip_signature_block(text) == text


class TestChunkDisclosureStripsSignatureBlock:
    """chunk_disclosure() 통합 — 두 문서 포맷·폴백 경로 공통으로 적용되는지 확인."""

    def test_structured_xml_section_has_signature_removed(self) -> None:
        xml = """<?xml version="1.0" encoding="utf-8"?>
<DOCUMENT>
<DOCUMENT-NAME>주요사항보고서(유상증자결정)</DOCUMENT-NAME>
<BODY>
<SECTION-1 ACLASS="MANDATORY">
<TITLE>유상증자 결정</TITLE>
<P>발행가액은 이사회 결의로 확정된다. (공시책임자) 재무지원본부장 김양석 ※ 관련공시 -</P>
</SECTION-1>
</BODY>
</DOCUMENT>
"""
        sections = chunk_disclosure(xml.encode("utf-8"), rcept_no="R1", corp_code="C1")

        assert len(sections) == 1
        assert "김양석" not in sections[0].text
        assert "발행가액은 이사회 결의로 확정된다." in sections[0].text

    def test_section_that_is_purely_contact_info_is_dropped(self) -> None:
        """섹션 하나가 순수 연락처 블록뿐이면(다른 섹션은 남아있는 상태) 그 섹션은
        버려진다 — HTML 경로의 "빈 표는 건너뛴다"와 같은 원칙."""
        xml = """<?xml version="1.0" encoding="utf-8"?>
<DOCUMENT>
<DOCUMENT-NAME>주요사항보고서(유상증자결정)</DOCUMENT-NAME>
<BODY>
<SECTION-1 ACLASS="MANDATORY">
<TITLE>유상증자 결정</TITLE>
<P>발행가액은 이사회 결의로 확정된다.</P>
</SECTION-1>
<SECTION-1 ACLASS="MANDATORY">
<TITLE>연락처</TITLE>
<P>(공시책임자) 재무지원본부장 김양석</P>
</SECTION-1>
</BODY>
</DOCUMENT>
"""
        sections = chunk_disclosure(xml.encode("utf-8"), rcept_no="R1", corp_code="C1")

        assert len(sections) == 1
        assert sections[0].section_title == "유상증자 결정"

    def test_all_sections_purely_contact_info_falls_back_to_original(self) -> None:
        """극단적 케이스 — 모든 섹션이 통째로 사라지면(순수 연락처만 있던 문서)
        원본을 그대로 보존한다("조용히 정보를 버리지 않는다" 원칙, 이 파일 전체 원칙)."""
        xml = """<?xml version="1.0" encoding="utf-8"?>
<DOCUMENT>
<DOCUMENT-NAME>주요사항보고서(유상증자결정)</DOCUMENT-NAME>
<BODY>
<SECTION-1 ACLASS="MANDATORY">
<TITLE>연락처</TITLE>
<P>(공시책임자) 재무지원본부장 김양석</P>
</SECTION-1>
</BODY>
</DOCUMENT>
"""
        sections = chunk_disclosure(xml.encode("utf-8"), rcept_no="R1", corp_code="C1")

        assert len(sections) == 1
        assert "김양석" in sections[0].text  # 원본 그대로 보존

    def test_html_table_has_signature_removed(self) -> None:
        html = """<html><head><title>테스트회사/공시</title></head>
<body>
<div class="xforms_title"><div><span>단일판매공급계약체결(자율공시)</span></div></div>
<table><tbody>
<tr><td>1. 계약 구분</td><td>상품공급</td></tr>
<tr><td>공시책임자(성명)</td><td>경영관리부장 이강현</td></tr>
</tbody></table>
</body></html>
"""
        sections = chunk_disclosure(html.encode("utf-8"), rcept_no="R1", corp_code="C1")

        assert len(sections) == 1
        assert "이강현" not in sections[0].text
        assert "상품공급" in sections[0].text
