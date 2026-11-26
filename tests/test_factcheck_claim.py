"""factcheck/claim.py::extract_claim 단위 테스트. LLM 호출 없음(monkeypatch)."""

from __future__ import annotations

import pytest

import disclosure_lit.factcheck.claim as claim_module
from disclosure_lit.factcheck.claim import Claim, _ClaimExtraction, extract_claim
from disclosure_lit.llm import LLMResult


def _fake_complete(parsed: _ClaimExtraction | None):
    def _complete(system: str, user: str, *, tier: str, schema) -> LLMResult:
        return LLMResult(text="", parsed=parsed, input_tokens=0, output_tokens=0)

    return _complete


class TestExtractClaim:
    def test_extracts_corp_name_and_keyword(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            claim_module,
            "complete",
            _fake_complete(
                _ClaimExtraction(corp_name="삼성전자", keyword="2차전지 신사업 MOU 체결")
            ),
        )

        claim = extract_claim("삼성전자, 2차전지 신사업 MOU 체결설 돈다")

        assert claim == Claim(
            corp_name="삼성전자",
            corp_code=None,
            keyword="2차전지 신사업 MOU 체결",
            raw_text="삼성전자, 2차전지 신사업 MOU 체결설 돈다",
        )

    def test_corp_name_none_when_no_company_identified(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """종목이 특정 안 되는 문장(화법 문구 단독 검색 경로)."""
        monkeypatch.setattr(
            claim_module,
            "complete",
            _fake_complete(_ClaimExtraction(corp_name=None, keyword="유상증자")),
        )

        claim = extract_claim("유상증자 하면 항상 주가가 빠지던데")

        assert claim.corp_name is None
        assert claim.keyword == "유상증자"

    def test_corp_code_never_filled_by_llm(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """corp_code는 이 함수 책임이 아니다(Issue #32/DEC-005 — resolve_corp_code가 담당).

        `_ClaimExtraction`에 corp_code 필드 자체가 없어서 LLM이 뭘 리턴하든 항상 None."""
        monkeypatch.setattr(
            claim_module,
            "complete",
            _fake_complete(_ClaimExtraction(corp_name="아무회사", keyword="루머")),
        )

        claim = extract_claim("아무회사 루머")

        assert claim.corp_code is None

    def test_falls_back_when_llm_returns_unparsed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """LLM이 스키마 파싱에 실패해 parsed=None을 돌려줘도 크래시하지 않는다."""
        monkeypatch.setattr(claim_module, "complete", _fake_complete(None))

        claim = extract_claim("알 수 없는 루머 문장")

        assert claim.corp_name is None
        assert claim.keyword == "알 수 없는 루머 문장"
        assert claim.raw_text == "알 수 없는 루머 문장"
