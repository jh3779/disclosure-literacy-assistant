"""루머 텍스트 → 클레임 추출 — L2a. 담당: B(김가빈)."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel

from disclosure_lit.llm import complete

_SYSTEM_PROMPT = """당신은 주식 커뮤니티에 도는 루머/소문 문장에서 사실 확인에 필요한
정보만 뽑아내는 추출기입니다. 판단이나 평가는 하지 않습니다.

- corp_name: 문장이 특정 상장회사를 지목하면 그 회사명(문장에 쓰인 표현 그대로, 예:
  "삼성전자", "OO전자"처럼 약칭/placeholder면 그대로). 특정 회사를 알 수 없으면 null.
- keyword: 이 루머의 핵심 주장을 검색에 쓸 수 있는 짧은 구(5~15자 안팎)로 요약. 예:
  "2차전지 신사업 MOU 체결", "유상증자", "임원 지분 매각". 문장을 그대로 베끼지 말고
  핵심만 추리세요.

절대 하지 않을 것: 종목코드 추정(회사명만 알아도 됨, 코드는 별도 조회함), 사실 여부 판단,
투자 의견."""


class _ClaimExtraction(BaseModel):
    corp_name: str | None
    keyword: str


@dataclass
class Claim:
    corp_name: str | None
    corp_code: str | None
    keyword: str
    raw_text: str


def extract_claim(rumor_text: str) -> Claim:
    """루머 문장에서 종목명/핵심 키워드를 추출한다.

    `corp_code`는 여기서 채우지 않는다 — 종목명→코드 변환은 `pipeline.py::resolve_corp_code`
    가 `CorpCodeIndex`로 별도 처리한다(Issue #32/DEC-005). LLM에게 DART corp_code(8자리)
    추정까지 맡기면 근거 없이 지어낼 위험이 있어서 애초에 범위에서 뺐다.
    """
    result = complete(_SYSTEM_PROMPT, rumor_text, tier="mini", schema=_ClaimExtraction)
    parsed = result.parsed or _ClaimExtraction(corp_name=None, keyword=rumor_text)
    return Claim(
        corp_name=parsed.corp_name,
        corp_code=None,
        keyword=parsed.keyword,
        raw_text=rumor_text,
    )
