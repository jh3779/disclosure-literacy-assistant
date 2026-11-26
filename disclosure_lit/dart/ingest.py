"""공시 원문 → 서식 유형별 섹션 청킹 — L0. 담당: A(이승노)."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from html.parser import HTMLParser

from lxml import etree

FALLBACK_FORM_TYPE = "기타"

# report_nm 앞에 붙는 대괄호 태그([기재정정], [첨부정정], [발행조건확정] 등) 전부 제거.
_PREFIX_TAGS = re.compile(r"^(\[[^\]]*\]\s*)+")


def _clean_title(report_nm: str) -> str:
    return _PREFIX_TAGS.sub("", report_nm.strip()).strip()


# 순서가 중요하다 — 위에서부터 첫 매치가 채택된다(더 구체적인 규칙을 먼저 둔다).
#
# 방향이 반대인 이벤트(취득/처분, 유상/무상, 변경/routine 보고)는 같은 키워드를 공유해도
# 절대 하나로 합치지 않는다 — 합치면 "부정/긍정 사실 동일 비중" 원칙(CLAUDE.md 주의점 2)이
# 이 라벨 단계에서부터 깨진다. 이 라벨은 L2b 의 메타데이터 필터용일 뿐, 사실 판단(L2a)의
# 근거로 쓰지 않는다.
# 실제 시장 데이터(scripts/form_type_coverage.py, 코스피+코스닥 최근 2주 1,000건) 기준
# 빈도 상위 항목을 반영해 보강했다 — 처음 버전은 미분류율 52%였다.
_RULES: list[tuple[str, Callable[[str], bool]]] = [
    ("유상증자", lambda t: "유상증자" in t),
    ("무상증자", lambda t: "무상증자" in t),
    ("감자", lambda t: "감자" in t),
    ("자기주식_취득", lambda t: "자기주식" in t and "취득" in t),
    ("자기주식_처분", lambda t: "자기주식" in t and "처분" in t),
    ("타법인출자_취득", lambda t: "타법인" in t and "취득" in t),
    ("타법인출자_처분", lambda t: "타법인" in t and "처분" in t),
    ("최대주주_변경", lambda t: "최대주주" in t and ("변경" in t or "양수도계약" in t)),
    ("최대주주_지분보고", lambda t: "최대주주" in t and "소유주식변동" in t),
    ("임원ㆍ주요주주_거래계획", lambda t: "특정증권" in t and "거래계획보고서" in t),
    ("임원ㆍ주요주주_지분보고", lambda t: "특정증권" in t and "소유상황보고서" in t),
    ("대량보유_지분보고", lambda t: "대량보유상황보고서" in t),
    ("전환사채_발행", lambda t: "전환사채" in t and "발행" in t),
    ("전환사채_조기취득", lambda t: "전환사채" in t and "취득" in t),
    ("전환사채_매도", lambda t: "전환사채" in t and "매도" in t),
    ("신주인수권부사채_발행", lambda t: "신주인수권부사채" in t and "발행" in t),
    ("공급계약_해지", lambda t: ("단일판매" in t or "공급계약" in t) and "해지" in t),
    ("공급계약_체결", lambda t: ("단일판매" in t or "공급계약" in t) and "체결" in t),
    ("신탁계약_해지", lambda t: "신탁계약" in t and "해지" in t),
    ("신탁계약_취득", lambda t: "신탁계약" in t and "취득" in t),
    (
        "증권발행_신고",
        lambda t: any(k in t for k in ("투자설명서", "증권신고서", "일괄신고추가서류")),
    ),
    ("증권발행실적보고", lambda t: "증권발행실적보고서" in t),
    ("주주총회_소집", lambda t: "주주총회" in t and "소집" in t),
    ("주주총회_결과", lambda t: "주주총회" in t and "결과" in t),
    ("의결권대리행사권유", lambda t: "의결권대리행사" in t),
    ("채무보증", lambda t: "채무보증" in t),
    ("주주명부_기준일", lambda t: "주주명부" in t and ("폐쇄" in t or "기준일" in t)),
    ("주식매수선택권_부여", lambda t: "주식매수선택권" in t),
    ("소액공모", lambda t: "소액공모" in t),
    ("주식소각", lambda t: "주식소각" in t),
    ("회사합병", lambda t: "합병" in t),
    ("거래재개", lambda t: ("매매거래정지" in t or "거래정지" in t) and "해제" in t),
    ("거래정지", lambda t: "매매거래정지" in t or "거래정지" in t),
    ("제재_벌금", lambda t: any(k in t for k in ("벌금", "과태료", "과징금"))),
    ("영업정지", lambda t: "영업정지" in t),
    ("MOU", lambda t: "양해각서" in t or "MOU" in t.upper()),
    (
        "임원변경",
        lambda t: (
            ("임원" in t or "이사" in t) and any(k in t for k in ("선임", "해임", "변경", "퇴임"))
        ),
    ),
    ("IR_기업설명회", lambda t: "기업설명회" in t or "IR" in t.upper()),
    ("정기공시", lambda t: any(k in t for k in ("사업보고서", "분기보고서", "반기보고서"))),
]


@dataclass
class DisclosureSection:
    rcept_no: str
    corp_code: str
    form_type: str  # 예: "MOU", "유상증자", "최대주주_변경"
    section_title: str
    text: str
    # DartClient.list_disclosures() 의 DisclosureMeta.rcept_dt 를 호출부가 채워 넣는다
    # (Issue #32/DEC-005) — 기본값 "" 은 값을 안 넘긴 기존 호출부(테스트 등)를 위한
    # 하위호환용이지 "날짜 없음"을 의미로 쓰라는 뜻이 아니다. CompanyDisclosureIndex.search()
    # 가 이 값으로 최신순 정렬한다.
    rcept_dt: str = ""


def normalize_form_type(report_nm: str) -> str:
    """공시서식 제목에서 표준 서식 유형(form_type)을 추출한다.

    `report_nm` 은 DART 가 정한 서식 이름이라 어느 정도 고정 어휘이긴 하지만, 완전히
    구조화된 코드가 아니라 제목 문자열이다(list.json 응답 자체에 유형 코드는 없음). 그래서
    이 함수는 "정확한 분류기"가 아니라 **보수적인 1차 라벨**이다:

    - 정정·첨부 태그(`[기재정정]` 등)와 공백을 먼저 제거한다
    - 방향이 반대인 이벤트(취득 vs 처분, 최대주주 실제 변경 vs 단순 지분 변동 보고 등)는
      키워드가 겹쳐도 별개 카테고리로 유지한다 — 합치면 사실상 암묵적 판단이 라벨에
      새어 들어간다
    - 매칭 실패 시 조용히 버리지 않고 `"기타"` 를 반환한다 — 미분류 비율을 추적해서
      테이블을 넓힐지 판단하는 근거로 쓴다(예: scripts/form_type_coverage.py)
    """
    title = _clean_title(report_nm)
    for form_type, matches in _RULES:
        if matches(title):
            return form_type
    return FALLBACK_FORM_TYPE


FALLBACK_SECTION_TITLE = "본문"

# 실제 원문을 조사해보면 DART document.xml 응답이 두 개의 완전히 다른 포맷으로 온다
# (조사 기록: A6 PR 본문 참고):
#   Family A — <DOCUMENT><BODY><SECTION-1><TITLE>...</TITLE>...</SECTION-1> 구조화 XML
#   Family B — <html><head>...<body>...<table>... 진짜 HTML(자율공시류에 많음)
#
# 게다가 Family A 조차 표준 XML이 아니다. 처음엔 "&" 미이스케이프만 잡았는데(정규식으로
# &amp; 치환), 실제로 더 넓혀 조사해보니 그걸로는 부족했다 — 속성값 안에 이스케이프 안 된
# 큰따옴표가 그대로 들어있는 경우도 있었다(예: `ENG="...the entity's "projected
# quantity"..."` — 속성이 조기 종료됨). 실측 30건 기준 표준 파서(xml.etree, & 만 수동
# 이스케이프)는 43%만 파싱에 성공했다. `lxml.etree` 의 recover 모드(HTML 파서처럼 관대하게
# 복구)로 바꾸니 같은 30건이 100% 파싱됐다 — DART 원문이 애초에 "브라우저가 봐주는 수준"
# 이지 표준 XML이 아니라는 뜻이라, 정규식으로 하나씩 땜질하는 대신 관대한 파서를 썼다.
#
# 다만 `&` 이스케이프 정규식은 여전히 먼저 돌린다 — lxml recover 모드 단독으로는 이스케이프
# 안 된 "&"를 오류로 스킵하면서 **글자 자체를 조용히 지워버린다**("R&D" -> "R", "NH
# INVESTMENT & SECURITIES" -> "NH INVESTMENT SECURITIES")는 걸 직접 확인했다. 구조적
# 깨짐(따옴표·태그 불일치)은 lxml recover 가, 내용 보존이 필요한 "&"는 정규식이 먼저
# 처리하는 두 겹 방어로 간다.
_UNESCAPED_AMP = re.compile(r"&(?!(amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)")

# Family B에서 표 바로 앞에 오는 "【소제목】" 형태의 헤딩 — 있으면 section_title로 쓴다.
_BRACKET_HEADING = re.compile(r"^【(.+)】$")

_STRIP_TAGS = re.compile(r"<[^>]+>")

# 서명란/연락처 블록 제거(Issue #60) — 실측(data/raw/*/sections.jsonl, "공시책임자"
# 포함 98건)으로 확인한 두 갈래:
#   Type A(제거 대상, 서식 말미 서명란/연락처칸): "(공시책임자) 박만준",
#     "[공시책임자] 교보증권 재무지원본부장 김양석", "공시책임자(성명) 경영관리부장
#     이강현", "공시책임자명(전화번호) ...(02-840-6871)" — 라벨이 괄호로 감싸여
#     있거나(`(라벨)`/`[라벨]`) "(성명)"/"(전화번호)" 필드로 바로 붙어 있다.
#   Type B(보존 대상, 본문 서술): "공시책임자 1인과 공시담당자 2인으로 구성된...",
#     "공시서류를 작성하여 공시책임자에게 보고하며..." — 라벨이 항상 괄호 없이
#     조사(은/는/에게/의 등)와 함께 문장 속에 등장한다.
# 두 갈래가 "라벨에 괄호가 바로 붙어 있는가"로 명확히 갈리므로, 그 형태만 매칭해서
# Type B 정상 문장은 절대 건드리지 않는다. 매칭된 라벨 뒤 값(이름·전화번호 등)은
# 다음 라벨·"※"·"N. " 번호 목록 구분자·문자열 끝 중 가장 먼저 나오는 지점까지
# 비탐욕적으로 함께 지운다(실측 샘플 전부 이 경계 안에서 값이 끝남).
_SIGNATURE_LABEL = r"공시책임자|공시담당자"
_SIGNATURE_FIELD = re.compile(
    rf"(?:[(\[]\s*(?:{_SIGNATURE_LABEL})\s*[)\]]|(?:{_SIGNATURE_LABEL})명?\s*\((?:성명|전화번호)\))"
    rf".*?(?=(?:{_SIGNATURE_LABEL})|※|\d+\.\s|$)",
    re.DOTALL,
)


def _strip_signature_block(text: str) -> str:
    """서식 말미 서명란/연락처 블록을 지운다(Issue #60, `_SIGNATURE_FIELD` 참고).

    L2a 팩트체크 엔진이 청크에 실제로 존재하는 서명란 이름을 사실로 추출하면,
    근거가 있는데도 gold set 정답 목록에는 없어 "지어냄(hallucination)"으로 잘못
    채점될 위험이 있었다(PR #59 리뷰 후속) — 정답 쪽에서만 지우는 임시방편 대신
    원천(청킹 단계)에서 제거해 새로 수집하는 공시에도 같은 문제가 재발하지 않게 한다.
    """
    return _SIGNATURE_FIELD.sub("", text).strip()


def _decode(raw: bytes) -> str:
    # 실측 확인: <meta charset="euc-kr"> 태그가 붙어있어도 실제 바이트는 UTF-8 이었다
    # (여러 샘플에서 strict UTF-8 디코딩 성공) — meta 태그를 믿지 않고 항상 UTF-8로 읽는다.
    return raw.decode("utf-8", errors="replace")


def _fallback_single_section(
    raw: bytes, *, rcept_no: str, corp_code: str, form_type: str, rcept_dt: str
) -> list[DisclosureSection]:
    """구조를 못 알아낼 때 태그만 벗겨낸 전체 텍스트를 섹션 하나로 반환한다.

    조용히 정보를 버리는 대신, 최소한 텍스트는 보존하고 section_title로 "미분할"임을
    남긴다 — normalize_form_type 의 "기타" 폴백과 같은 원칙.
    """
    text = re.sub(r"\s+", " ", _STRIP_TAGS.sub(" ", _decode(raw))).strip()
    return [
        DisclosureSection(
            rcept_no=rcept_no,
            corp_code=corp_code,
            form_type=form_type,
            section_title=f"{FALLBACK_SECTION_TITLE}(미분할)",
            text=text,
            rcept_dt=rcept_dt,
        )
    ]


def _chunk_structured_xml(
    raw: bytes, *, rcept_no: str, corp_code: str, rcept_dt: str
) -> list[DisclosureSection]:
    """Family A(dart4.xsd 구조화 XML) — <SECTION-1> 을 섹션 경계로 삼는다.

    `&` 를 먼저 이스케이프한 뒤(내용 보존, 위 `_UNESCAPED_AMP` 주석 참고) `lxml.etree` 를
    `recover=True` 로 파싱한다 — 그러면 그 외 구조적 깨짐(속성 안 따옴표, 태그 불일치)도
    복구된다. `_decode()` 가 잘못된 바이트 시퀀스를 치환 문자로 바꿔주므로 인코딩 자체가
    깨진 극단적인 경우도 여기서 걸러진다.
    """
    fixed_text = _UNESCAPED_AMP.sub("&amp;", _decode(raw))
    parser = etree.XMLParser(recover=True, encoding="utf-8")
    try:
        root = etree.fromstring(fixed_text.encode("utf-8"), parser=parser)
    except etree.XMLSyntaxError:
        # recover=True 로도 못 넘어가는 극단적인 경우(빈 내용 등) — lxml 이 예외를 던진다.
        root = None
    if root is None:
        return _fallback_single_section(
            raw,
            rcept_no=rcept_no,
            corp_code=corp_code,
            form_type=normalize_form_type(""),
            rcept_dt=rcept_dt,
        )

    report_nm = root.findtext(".//DOCUMENT-NAME") or ""
    form_type = normalize_form_type(report_nm)

    sections: list[DisclosureSection] = []
    for section_el in root.iter("SECTION-1"):
        title_el = section_el.find("TITLE")
        title = (title_el.text or "").strip() if title_el is not None else FALLBACK_SECTION_TITLE
        body_parts = ["".join(child.itertext()) for child in section_el if child is not title_el]
        body_text = re.sub(r"\s+", " ", " ".join(body_parts)).strip()
        sections.append(
            DisclosureSection(
                rcept_no=rcept_no,
                corp_code=corp_code,
                form_type=form_type,
                section_title=title or FALLBACK_SECTION_TITLE,
                text=body_text,
                rcept_dt=rcept_dt,
            )
        )

    if not sections:
        return _fallback_single_section(
            raw, rcept_no=rcept_no, corp_code=corp_code, form_type=form_type, rcept_dt=rcept_dt
        )
    return sections


def _render_table_row(cells: list[str]) -> str:
    """행 하나를 한 줄로 렌더링한다. 2열이면 "라벨: 값"으로, 그 외(헤더+데이터 행렬 등)는
    "셀1 | 셀2 | ..."로 — 실제 관측되는 Family B 표 대부분이 2열 라벨/값 구조라 그 경우엔
    읽기 편한 콜론 표기를 쓰고, 열이 더 많은 표(주주총회 안건 세부내역처럼 헤더+여러 데이터
    행으로 된 matrix)는 구분자로 셀을 구분해 열 순서를 보존한다."""
    if len(cells) == 2:
        return f"{cells[0]}: {cells[1]}"
    return " | ".join(cells)


def _render_table_rows(rows: list[list[str]]) -> str:
    """행 단위 줄바꿈을 유지한 채로 표 전체를 텍스트로 렌더링한다.

    이전 구현은 모든 셀 텍스트를 한 줄로 이어붙인 뒤 `re.sub(r"\\s+", " ", ...)`로
    공백을 뭉개면서 행 경계 자체를 지워버렸다. 2열 라벨/값 표(한 행 = 사실 하나)는 그래도
    "라벨1 값1 라벨2 값2..." 순서라 우연히 읽혔지만, 헤더 행 + 여러 데이터 행으로 된
    matrix 표(예: 주주총회 안건 세부내역 — "안건번호/안건내용/가결여부" 헤더에 데이터 행이
    여럿)는 셀이 한 줄로 뭉개지면서 어떤 값이 어떤 행·열에 속하는지 알 수 없게 됐다(실측
    확인). 행마다 줄을 나누고 셀 순서를 보존해 렌더링한다.
    """
    return "\n".join(_render_table_row(row) for row in rows if any(row))


class _TableSectionParser(HTMLParser):
    """Family B(HTML) 문서를 <table> 단위로 섹션화한다.

    실제 관측: 문서 전체가 2열(라벨/값) <table> 여러 개로 이루어져 있고, 그 안의
    "1. 라벨" 번호는 그 표 안에서만 유효한 로컬 번호다 — 서로 다른 표가 똑같이 1번부터
    다시 매기는 경우가 실제로 있었다(예: 임시주주총회결과 — 본문 표와 "세부내역" 표가
    각각 독립적으로 1,2,3... 을 씀). 그래서 번호 자체를 섹션 경계로 쓰지 않고 <table>
    엘리먼트 하나를 섹션 하나로 다룬다 — 표가 바뀌면 번호가 겹쳐도 섹션이 자동으로
    분리되어 서로 다른 항목이 섞이지 않는다.

    section_title 은 표 직전의 "【소제목】" 헤딩이 있으면 그걸 쓰고, 첫 번째 표라면 문서
    제목(class="xforms_title")을 쓰고, 그마저 없으면 "표 N"으로 명시 폴백한다.

    행/열 구조는 `<tr>`/`<td>`/`<th>` 단위로 유지한다(`_render_table_rows` 참고) — 셀
    텍스트를 모아뒀다가 행이 끝나면 `_rows`에, 표가 끝나면 그 `_rows`를 렌더링한다.
    """

    def __init__(self) -> None:
        super().__init__()
        self.tables: list[tuple[str, str]] = []
        self._pending_title: str | None = None
        self._doc_title: str | None = None
        self._expect_doc_title = False
        # 불리언이 아니라 depth 카운터인 이유(리뷰 지적, PR #34, @powercodingman):
        # HWP->HTML 변환 산출물에 레이아웃용 중첩 <table>이 흔하다. 불리언이면 안쪽
        # <table> 시작에서 _rows가 초기화되며 바깥 표가 모아둔 내용이 사라지고,
        # 안쪽 </table>에서 False로 꺼져버려 진짜 바깥 </table>이 아무 동작도 안 해
        # 바깥 표 자체가 유실된다 — 이 파일의 "조용히 정보를 버리지 않는다" 원칙 위반.
        self._table_depth = 0
        self._table_count = 0
        # 가장 바깥 표(depth==1)의 행/셀만 구조로 추적한다. 중첩 표(depth>=2)의 tr/td는
        # 별도로 추적하지 않고, 그 텍스트는 지금 열려 있는 바깥 셀(_current_cell_parts)에
        # 그대로 얹힌다 — 중첩 표 자체의 행/열 구조까지 살릴 필요는 없지만(레이아웃용이라
        # 의미가 없음), 내용을 잃지는 않는다(PR #34 원칙).
        self._rows: list[list[str]] = []
        self._current_row: list[str] | None = None
        self._current_cell_parts: list[str] | None = None

    @property
    def doc_title(self) -> str | None:
        """xforms_title div 에서 파싱한 실제 공시명(예: '단일판매공급계약체결(자율공시)').

        <head><title> 태그는 "회사명/제목/날짜제목" 식 브레드크럼이라 "체결" 같은
        분류에 결정적인 키워드가 빠질 수 있다(리뷰 지적, PR #26) — form_type 추정은
        이쪽을 우선 써야 한다.
        """
        return self._doc_title

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "div" and dict(attrs).get("class") == "xforms_title":
            self._expect_doc_title = True
        elif tag == "table":
            # 가장 바깥 <table>이 열릴 때만 초기화한다 — 중첩된 안쪽 <table>은 깊이만
            # 늘리고 바깥 표가 모아둔 내용은 그대로 유지한다.
            if self._table_depth == 0:
                self._rows = []
                self._current_row = None
                self._table_count += 1
            self._table_depth += 1
        elif tag == "tr" and self._table_depth == 1:
            self._current_row = []
        elif tag in ("td", "th") and self._table_depth == 1 and self._current_row is not None:
            self._current_cell_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "table" and self._table_depth > 0:
            self._table_depth -= 1
            if self._table_depth == 0:
                # 가장 바깥 </table>이 닫힐 때만 섹션을 확정한다.
                text = _render_table_rows(self._rows)
                title = self._pending_title or self._doc_title or f"표 {self._table_count}"
                self.tables.append((title, text))
                self._pending_title = None
        elif (
            tag in ("td", "th") and self._table_depth == 1 and self._current_cell_parts is not None
        ):
            cell_text = re.sub(r"\s+", " ", " ".join(self._current_cell_parts)).strip()
            if self._current_row is not None:
                self._current_row.append(cell_text)
            self._current_cell_parts = None
        elif tag == "tr" and self._table_depth == 1 and self._current_row is not None:
            if self._current_row:
                self._rows.append(self._current_row)
            self._current_row = None

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if not text:
            return
        if self._expect_doc_title:
            self._doc_title = text
            self._expect_doc_title = False
            return
        if self._current_cell_parts is not None:
            # 지금 열려 있는 바깥 셀 안(중첩 표 내용 포함, 위 클래스 docstring 참고).
            self._current_cell_parts.append(text)
            return
        if self._table_depth > 0:
            # <td>/<th> 없이 표 안에 직접 들어간 텍스트(HWP 변환 산출물 등 malformed
            # 케이스) — 조용히 버리지 않고 현재 행(또는 새 행)에 셀 하나로 얹는다.
            if self._current_row is not None:
                self._current_row.append(text)
            else:
                self._rows.append([text])
            return
        m = _BRACKET_HEADING.match(text)
        if m:
            self._pending_title = m.group(1).strip()


def _chunk_html_document(
    raw: bytes, *, rcept_no: str, corp_code: str, rcept_dt: str
) -> list[DisclosureSection]:
    """Family B(HTML) — <table> 단위로 청킹한다."""
    text = _decode(raw)

    parser = _TableSectionParser()
    parser.feed(text)
    parser.close()

    # form_type 추정용 report_nm 은 parser 가 xforms_title div 에서 파싱한 doc_title을
    # 우선 쓴다. <head><title> 태그("회사명/제목/날짜제목" 브레드크럼)로 폴백하는데,
    # 거기엔 "체결" 같은 분류에 결정적인 키워드가 없는 경우가 있다(리뷰 지적, PR #26 —
    # <title>만 쓰면 "공급계약_체결"이 "체결" 키워드 부재로 "기타"에 잘못 떨어졌었다).
    if parser.doc_title:
        report_nm = parser.doc_title
    else:
        title_match = re.search(r"<title>(.*?)</title>", text, re.IGNORECASE | re.DOTALL)
        report_nm = title_match.group(1) if title_match else ""
    form_type = normalize_form_type(report_nm)

    if not parser.tables:
        return _fallback_single_section(
            raw, rcept_no=rcept_no, corp_code=corp_code, form_type=form_type, rcept_dt=rcept_dt
        )

    return [
        DisclosureSection(
            rcept_no=rcept_no,
            corp_code=corp_code,
            form_type=form_type,
            section_title=title,
            text=body_text,
            rcept_dt=rcept_dt,
        )
        for title, body_text in parser.tables
        if body_text  # 빈 표(레이아웃용 등)는 건너뛴다
    ] or _fallback_single_section(
        raw, rcept_no=rcept_no, corp_code=corp_code, form_type=form_type, rcept_dt=rcept_dt
    )


def chunk_disclosure(
    raw_xml: bytes, *, rcept_no: str, corp_code: str, rcept_dt: str = ""
) -> list[DisclosureSection]:
    """공시 원문을 서식 표준 항목 경계로 청킹한다.

    DART `document.xml` 응답은 실제로 두 포맷이 섞여 온다(조사 결과, PR 본문 참고):
    구조화 XML(`<DOCUMENT><SECTION-1>`)과 진짜 HTML(`<html><table>`). 앞부분을 보고
    분기하고, 각 포맷에 맞는 전용 청커로 처리한다. 어느 쪽도 구조를 못 찾으면 태그만
    벗겨낸 전체 텍스트를 섹션 하나로 폴백한다(조용히 버리지 않음).

    `rcept_dt`(공시 접수일자, `DisclosureMeta.rcept_dt`)는 호출부가 알고 있으면 넘긴다 —
    `CompanyDisclosureIndex.search()`가 최신순 정렬에 쓴다(Issue #32/DEC-005). 기본값
    `""`은 값을 안 넘기는 기존 호출부(테스트 등) 하위호환용이다.
    """
    head = raw_xml[:200].decode("utf-8", errors="replace").lower()
    if "<html" in head:
        sections = _chunk_html_document(
            raw_xml, rcept_no=rcept_no, corp_code=corp_code, rcept_dt=rcept_dt
        )
    else:
        sections = _chunk_structured_xml(
            raw_xml, rcept_no=rcept_no, corp_code=corp_code, rcept_dt=rcept_dt
        )

    # 서명란 제거(Issue #60)는 두 포맷·폴백 경로 공통이라 여기 한 곳에서 적용한다.
    # 제거 후 텍스트가 통째로 비면(순수 연락처 표 하나가 섹션 전체였던 경우) 그
    # 섹션은 버린다 — 단, 전부 비어버리면(극단적 케이스) "조용히 정보를 버리지
    # 않는다" 원칙(이 파일 전체의 원칙)에 따라 원본을 그대로 보존한다.
    cleaned = []
    for section in sections:
        text = _strip_signature_block(section.text)
        if not text:
            continue
        section.text = text
        cleaned.append(section)
    return cleaned or sections
