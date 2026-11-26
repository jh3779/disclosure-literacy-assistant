"""factcheck/compliance.py — 투자 조언 표현 결정적 필터(규제 방어선 F1, 2026-09-22).

두 방향을 똑같이 중요하게 검사한다:

1. **막아야 하는 것** — 시스템이 투자 조언을 하는 발화.
2. **막으면 안 되는 것** — 코퍼스 82,876개 섹션 전수 검사에서 실제로 발견된 정상 공시
   표현. 특히 증권신고서의 법정 투자위험 고지("주가가 하락할 가능성")를 막으면 부정적
   사실만 골라 지우게 되어 "긍정/부정 동일 비중" 방어선을 오히려 깨뜨린다.

2번이 이 테스트의 핵심이다 — 필터를 넓히고 싶어질 때 여기서 걸리도록 한다.
"""

from __future__ import annotations

import pytest

import disclosure_lit.factcheck.verify as verify_module
from disclosure_lit.dart.ingest import DisclosureSection
from disclosure_lit.factcheck.compliance import find_investment_advice
from disclosure_lit.factcheck.verify import (
    _FactCandidate,
    _FactExtraction,
    _GroundednessCheck,
    verify_claim,
)
from disclosure_lit.llm import LLMResult

_INVESTMENT_ADVICE = [
    "현 시점은 매수 적기로 판단됩니다.",
    "이 종목에 대한 매수 추천이 유지된다.",
    "투자의견은 '비중확대'이다.",
    "지금 매수하세요.",
    "주가가 싸니 사세요.",
    "보유 물량은 매도해야 합니다.",
    # 2026-09-22 v2 — 팀 리뷰(@9abin)가 지적한 누락. 조사가 끼거나 어미가 바뀌면
    # v1은 전부 놓쳤다(서술형 240개 중 36개만 적중).
    "이 종목을 매수를 추천합니다.",
    "보유 물량의 매도를 권유합니다.",
    "지금 매수하시기 바랍니다.",
    "이 종목을 매수 권장드립니다.",
    "매도하실 것을 권고합니다.",
    "지금 매수하시길 권합니다.",
    "지금이 매수할 때입니다.",
    "비중 확대 의견을 제시한다.",
    # 2026-09-24 v3 — #115 리뷰(f56b1f5 기준)가 재현한 누락. 명사화 연결형과 "권하다".
    "매수하는 것을 추천합니다.",
    "매도할 것을 권고합니다.",
    "이 종목은 매수를 권합니다.",
    # 부정형이어도 시스템의 의견(비추천)이면 조언이다 — 고지문식 부정만 제외한다.
    "현 시점에서는 매수를 추천하지 않습니다.",
    "매수 추천은 아니지만 지금이 기회입니다.",
    "매수를 추천하는 것이 아니라 매도를 추천합니다.",
    "지금은 매수 적기가 아닙니다.",
    # 2026-09-24 v4 — 자체 리뷰(에이전트)가 찾은 누락. "투자의견"은 막으면서 같은
    # 성격의 증권가 등급 표현은 빠져 있었다. 전부 실코퍼스 82,876섹션에서 0회임을
    # `scripts/measure_corpus_terms.py`로 확인했으므로 막아도 정상 공시를 지우지 않는다.
    "이 종목에 대해 매수의견을 제시했다.",
    "매도의견으로 하향 조정했다.",
    "보유의견을 유지한다.",
    "매수 등급을 부여했다.",
    "매도 등급으로 변경했다.",
    "지금이 매수 기회입니다.",
    "지금은 매도 기회로 보입니다.",
]

# 전부 실제 코퍼스에서 발견된 정상 공시 표현이다(compliance.py 모듈 docstring의 실측).
_LEGITIMATE_DISCLOSURE_PHRASES = [
    "이익 창출 관점에서는 부문 당기순이익 목표가 450억원이었으나, 923억원을 달성했다.",
    "상품-재고평가충당금 (25,872)",
    "재단법인 제이더블유이종호재단",
    "사외적립자산의 기대수익률 가정 0.0393",
    "목표주가와 같은 시장조건은 부여한 지분상품의 공정가치에 반영된다.",
    "자기주식의 취득 목적을 '적정주가 확보를 통한 주식가치 제고'로 공시했다.",
    "사업결합으로 취득하는 무형자산은 매수시점의 공정가치로 계상한다.",
    "대규모 물량이 출회하여 주가가 하락할 가능성이 있습니다.",
    "ADR 상장에 따른 원주의 주가 상승을 기대하고 투자에 임하는 경우 예상과 다를 수 있다.",
    "추가적인 선박가격 상승 여력은 제한적인 수준에 그칠 수 있다.",
    "국민연금 등 기관들의 주식 편입비중 확대",
    # 법률 용어·정상 거래 — "매수"/"매도"라는 단어 자체는 막지 않는다
    "임직원에게 주식매수선택권 10만주를 부여하였다.",
    "자기주식 50만주를 장내 매수하기로 결정하였다.",
    "최대주주가 보유 지분 일부를 장외 매도하였다.",
    "공개매수 신고서를 제출하였다.",
    # 2026-09-22 v2 — 팀 리뷰(@9abin)의 오탐 지적. 시스템의 조언이 아니라 공시 내용을
    # 서술하는 문장이다. 코퍼스 등장은 0회지만 "의견을 수렴"은 545회라 형태는 현실적이다.
    "소액주주들의 투자 의견을 수렴하였다.",
    "기술적 지표상 매수 신호가 발생했다고 밝혔다.",
    # 신용평가 고지문의 정형구 — 코퍼스 2,125회. 막으면 정상 사실이 대량 삭제된다.
    "본 평가는 특정 회사채에 대한 투자를 추천하거나 원리금 상환을 보증하는 것이 아니다.",
    # 제품 리뷰 번역(21회) · 증권사 랩어카운트 서비스 설명(20회)
    "해외 매체가 자사 제품을 강력추천 제품으로 선정하였다.",
    "고객 투자성향에 따라 종목추천 서비스를 제공한다.",
    # 2026-09-24 v3 — #115 리뷰의 오탐 지적. 원문 "투자를 추천"을 LLM이 "매수를 추천"으로
    # 바꿔 요약하면 매칭된다 — 원문 0회만으로는 이 경로를 못 막는다.
    "본 신용평가는 해당 회사채의 매수를 추천하는 것이 아니다.",
    "본 평가는 해당 회사채의 매수를 추천하거나 원리금 상환을 보증하는 것이 아니다.",
    "이 보고서는 매수 권유가 아닙니다.",
    # "권하다" 추가로 생길 수 있는 오탐 — "권한"(authority)은 잡지 않는다.
    "이사회에 자기주식 매수 권한을 위임하였다.",
    # E군(등급 표현)의 요약문 오탐 — 원문 "공개매수에 찬동한다는 의견을 표명"(14회)을
    # LLM이 줄여 쓴 형태. 공개매수 의견표명서는 조언이 아니라 DART 서식이다.
    "대상회사 이사회는 공개매수 의견을 표명하였다.",
    "공개매수 의견표명서를 제출하였다.",
]


class TestFindInvestmentAdvice:
    @pytest.mark.parametrize("text", _INVESTMENT_ADVICE)
    def test_detects_investment_advice(self, text: str) -> None:
        assert find_investment_advice(text) is not None

    @pytest.mark.parametrize("text", _LEGITIMATE_DISCLOSURE_PHRASES)
    def test_does_not_flag_legitimate_disclosure_language(self, text: str) -> None:
        """실측된 오탐 후보 — 여기가 실패하면 패턴이 너무 넓어진 것이다."""
        assert find_investment_advice(text) is None


def _section(text: str = "본문") -> DisclosureSection:
    return DisclosureSection(
        rcept_no="R1", corp_code="00126380", form_type="유상증자", section_title="제목", text=text
    )


def _stub(draft: _FactExtraction, guard: _GroundednessCheck):
    def _complete(system: str, user: str, *, tier: str, schema) -> LLMResult:
        parsed = draft if schema is _FactExtraction else guard
        return LLMResult(text="", parsed=parsed, input_tokens=0, output_tokens=0)

    return _complete


class TestVerifyClaimDropsInvestmentAdvice:
    """guard(근거성·관련성 검사)를 통과해도 투자 조언 표현이 있으면 결정적으로 버린다."""

    def test_advice_fact_is_dropped_even_if_guard_passes_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        draft = _FactExtraction(
            items=[
                _FactCandidate(
                    fact="500억원 유상증자를 결정했다.", section_index=0, polarity="positive"
                ),
                _FactCandidate(
                    fact="유상증자로 매수 적기라는 판단이다.", section_index=0, polarity="positive"
                ),
            ]
        )
        guard = _GroundednessCheck(grounded_indices=[0, 1])  # guard가 둘 다 통과시킴
        monkeypatch.setattr(verify_module, "complete", _stub(draft, guard))

        result = verify_claim("유상증자", [_section()])

        assert [i.fact for i in result.items] == ["500억원 유상증자를 결정했다."]

    def test_negative_risk_disclosure_is_kept(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """법정 투자위험 고지는 부정적 사실의 핵심 출처다 — 이걸 거르면 비중이 기운다."""
        draft = _FactExtraction(
            items=[
                _FactCandidate(
                    fact="신주 상장 시 대규모 물량 출회로 주가가 하락할 가능성이 있다고 밝혔다.",
                    section_index=0,
                    polarity="negative",
                ),
            ]
        )
        guard = _GroundednessCheck(grounded_indices=[0])
        monkeypatch.setattr(verify_module, "complete", _stub(draft, guard))

        result = verify_claim("유상증자", [_section()])

        assert len(result.items) == 1
        assert result.items[0].polarity == "negative"

    def test_filter_applies_equally_to_both_polarities(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """한쪽 polarity만 거르면 그 자체로 비중이 기운다."""
        draft = _FactExtraction(
            items=[
                _FactCandidate(
                    fact="이 종목을 매수를 추천합니다.", section_index=0, polarity="positive"
                ),
                _FactCandidate(
                    fact="보유 물량의 매도를 권유합니다.", section_index=0, polarity="negative"
                ),
            ]
        )
        guard = _GroundednessCheck(grounded_indices=[0, 1])
        monkeypatch.setattr(verify_module, "complete", _stub(draft, guard))

        result = verify_claim("유상증자", [_section()])

        assert result.items == []

    def test_nominalized_advice_is_dropped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """#115 리뷰 P1 재현 — v2까지는 두 문장 모두 최종 사실 목록에 남았다."""
        draft = _FactExtraction(
            items=[
                _FactCandidate(
                    fact="매수하는 것을 추천합니다.", section_index=0, polarity="positive"
                ),
                _FactCandidate(
                    fact="매도할 것을 권고합니다.", section_index=0, polarity="negative"
                ),
            ]
        )
        guard = _GroundednessCheck(grounded_indices=[0, 1])
        monkeypatch.setattr(verify_module, "complete", _stub(draft, guard))

        result = verify_claim("유상증자", [_section()])

        assert result.items == []
        assert result.n_dropped_advice == 2

    def test_disclaimer_denying_advice_is_kept(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """#115 리뷰 P2 재현 — 조언을 부정하는 신용평가 고지문 요약은 정상 사실이다."""
        fact = "본 신용평가는 해당 회사채의 매수를 추천하는 것이 아니다."
        draft = _FactExtraction(
            items=[_FactCandidate(fact=fact, section_index=0, polarity="neutral")]
        )
        guard = _GroundednessCheck(grounded_indices=[0])
        monkeypatch.setattr(verify_module, "complete", _stub(draft, guard))

        result = verify_claim("회사채", [_section()])

        assert [i.fact for i in result.items] == [fact]
        assert result.n_dropped_advice == 0

    def test_dropped_fact_is_logged(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """조용히 사라지면 프롬프트가 지시를 어기고 있다는 신호를 놓친다."""
        draft = _FactExtraction(
            items=[_FactCandidate(fact="지금 매수하세요.", section_index=0, polarity="positive")]
        )
        guard = _GroundednessCheck(grounded_indices=[0])
        monkeypatch.setattr(verify_module, "complete", _stub(draft, guard))

        with caplog.at_level("WARNING", logger=verify_module.logger.name):
            verify_claim("유상증자", [_section()])

        assert any("투자 조언 표현" in r.message for r in caplog.records)


class TestNextUpdateNoteIsAlsoChecked:
    """PR #114가 추가한 `next_update_note`도 LLM이 만들어 화면(📅 캡션)에 그대로 나간다."""

    def test_advice_in_note_removes_only_the_note(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """보조 메모 하나 때문에 멀쩡한 공시 사실을 잃으면 안 된다 — 메모만 지운다."""
        draft = _FactExtraction(
            items=[
                _FactCandidate(
                    fact="유상증자를 검토 중이라고 밝혔다.",
                    section_index=0,
                    polarity="neutral",
                    is_confirmed=False,
                    next_update_note="확정 전이니 지금이 매수 적기",
                ),
            ]
        )
        guard = _GroundednessCheck(grounded_indices=[0])
        monkeypatch.setattr(verify_module, "complete", _stub(draft, guard))

        result = verify_claim("유상증자", [_section()])

        assert len(result.items) == 1
        assert result.items[0].fact == "유상증자를 검토 중이라고 밝혔다."
        assert result.items[0].next_update_note is None
        assert result.items[0].is_confirmed is False  # 다른 필드는 그대로

    def test_ordinary_schedule_note_is_kept(self, monkeypatch: pytest.MonkeyPatch) -> None:
        draft = _FactExtraction(
            items=[
                _FactCandidate(
                    fact="유상증자를 검토 중이라고 밝혔다.",
                    section_index=0,
                    polarity="neutral",
                    is_confirmed=False,
                    next_update_note="3개월 이내 재공시 예정",
                ),
            ]
        )
        guard = _GroundednessCheck(grounded_indices=[0])
        monkeypatch.setattr(verify_module, "complete", _stub(draft, guard))

        result = verify_claim("유상증자", [_section()])

        assert result.items[0].next_update_note == "3개월 이내 재공시 예정"


class TestSellRumorFactCheckingIsNotBlocked:
    """PR #114의 추출 프롬프트는 "매도설"을 시점 비교로 판정하게 한다 — 그 결과 문장은
    루머 검증이지 투자 조언이 아니므로 걸리면 안 된다(프롬프트의 예시 문장 그대로)."""

    @pytest.mark.parametrize(
        "text",
        [
            "이용진(대표이사)의 지분율은 2025년 9월 67.53%에서 2026년 1월 67.78%로 오히려 늘어,"
            " 매도설과 배치된다",
            "지분 감소는 장내매도가 아니라 증여에 따른 것이다",
            "최대주주가 시간외매매로 매도하였다",
        ],
    )
    def test_rumor_verification_language_passes(self, text: str) -> None:
        assert find_investment_advice(text) is None


class TestParticleAndEndingVariants:
    """2026-09-22 v2 — v1의 누락 원인을 구조적으로 고정한다.

    v1은 `매수\\s*추천`처럼 **공백만** 허용하고 어미를 하드코딩해서, 목적격 조사가
    끼거나("매수를 추천") 어미가 바뀌면("권장드립니다") 전부 놓쳤다. 내가 떠올린 예시
    8개가 공교롭게 전부 조사 없는 형태여서 이 맹점을 스스로 보지 못했다 —
    그래서 예시 나열이 아니라 **조합으로 생성해** 검증한다.
    """

    _ACTS = ("매수", "매도")
    # v3: 명사화 연결형 추가(#115 리뷰 P1 — "하는 것을", "할 것을" 등을 v2가 놓쳤다).
    _PARTICLES = (
        "",
        "를",
        "을",
        "하기를",
        "하기",
        "하길",
        "하시기를",
        "하실 것을",
        "하는 것을",
        "하는 걸",
        "할 것을",
        "할 걸",
        "하시는 것을",
        "하시는 걸",
    )
    _ADVICE = ("추천", "권유", "권고", "권장")
    _ENDINGS = ("합니다", "한다", "드립니다", "드려요", "해 드립니다")

    def test_every_particle_ending_combination_is_caught(self) -> None:
        missed = [
            f"이 종목을 {act}{particle} {advice}{ending}"
            for act in self._ACTS
            for particle in self._PARTICLES
            for advice in self._ADVICE
            for ending in self._ENDINGS
            if find_investment_advice(f"이 종목을 {act}{particle} {advice}{ending}") is None
        ]
        assert missed == [], f"{len(missed)}개 조합을 놓침 — 예: {missed[:5]}"

    @pytest.mark.parametrize("ending", ["합니다", "해 드립니다", "하는 바입니다", "함"])
    def test_verb_gwonhada_is_caught(self, ending: str) -> None:
        """v3: "권하다"는 명사 "권"+어미라 A군 명사 목록으로는 못 잡았다."""
        for act in self._ACTS:
            for particle in self._PARTICLES:
                assert find_investment_advice(f"이 종목은 {act}{particle} 권{ending}") is not None

    @pytest.mark.parametrize(
        "ending",
        [
            "하세요",
            "하십시오",
            "하시기 바랍니다",
            "하시길 권합니다",
            "하시는 것을 권합니다",
            "해야 합니다",
            "합시다",
            "하시죠",
            "하기 바랍니다",
            "할 때입니다",
            "하실 타이밍입니다",
        ],
    )
    def test_imperative_and_propositive_endings_are_caught(self, ending: str) -> None:
        for act in self._ACTS:
            assert find_investment_advice(f"지금 {act}{ending}") is not None


class TestEmphasisAndInvisibleCharsDoNotBypass:
    """2026-09-23 다른 각도 점검 — 화면이 `st.markdown()`으로 렌더링하므로 LLM이 강조를
    넣는 건 자연스러운 일이다. `"**매수**를 추천합니다"`가 패턴을 그대로 통과했다.

    악의적 우회가 아니라 평범하게 일어날 수 있는 경우라 매칭 전에 걷어낸다.
    """

    @pytest.mark.parametrize(
        "text",
        [
            "**매수**를 추천합니다.",
            "매수를 *추천*합니다.",
            "__매도__를 권유합니다.",
            "`매수` 적기입니다.",
            "~~매도~~를 권고합니다.",
            "​매수를 추천합니다.",  # zero-width space
            "매수﻿를 추천합니다.",  # BOM
        ],
    )
    def test_emphasis_and_invisible_chars_are_still_caught(self, text: str) -> None:
        assert find_investment_advice(text) is not None

    def test_normalization_does_not_create_new_false_positives(self) -> None:
        """`_`를 지우면 단어가 붙는다 — 공시 유형명·회계 용어가 새로 걸리면 안 된다.
        (코퍼스 82,876개 섹션 전수로도 정규화 후 적중 0건을 확인했다.)"""
        for text in _LEGITIMATE_DISCLOSURE_PHRASES:
            assert find_investment_advice(text) is None, text
        for text in ["전환사채_발행 유형에 속한다.", "주식매수선택권_부여 공시이다."]:
            assert find_investment_advice(text) is None, text

    def test_returned_match_is_for_diagnosis_only(self) -> None:
        """반환값은 정규화된 사본에서 잘라낸 것이라 원문과 글자가 다를 수 있다 —
        로그·진단용이라는 계약을 고정한다(화면 텍스트를 바꾸는 함수가 아니다)."""
        assert find_investment_advice("**매수**를 추천합니다.") == "매수를 추천"


class TestDemoSampleFactsAreCompliant:
    """2026-09-23 다른 각도 점검 — `demo/app.py::_SAMPLE_FACT_CHECK`는 하드코딩된 사실이라
    `verify_claim()`을 거치지 않고 화면에 나간다. 지금 내용은 문제없지만 **아무도 검사하지
    않는다**는 게 문제였다.

    용어 사전(`glossary.py`)은 `_BANNED_WORDS`로 테스트가 전수 검사하는데, 샘플 팩트만
    빠져 있었다 — "정적 텍스트는 코드로 검사한다"는 원칙의 구멍을 메운다.
    """

    def test_sample_fact_check_items_pass_the_filter(self) -> None:
        from disclosure_lit.demo.app import _SAMPLE_FACT_CHECK

        for item in _SAMPLE_FACT_CHECK.items:
            assert find_investment_advice(item.fact) is None, item.fact
            if item.next_update_note:
                assert find_investment_advice(item.next_update_note) is None, item.next_update_note

    def test_sample_claim_text_passes_the_filter(self) -> None:
        from disclosure_lit.demo.app import _SAMPLE_FACT_CHECK

        assert find_investment_advice(_SAMPLE_FACT_CHECK.claim) is None


class TestNegatedDisclaimer:
    """v3: 조언 행위 뒤의 고지문식 부정은 제외하되, 시스템 의견인 부정은 계속 잡는다."""

    @pytest.mark.parametrize(
        "text",
        [
            "본 공시는 매도를 권유하려는 취지가 아님을 밝혔다.",
            "매수를 권하는 것이 아니다.",
            "이 자료는 매수 추천이나 보증이 아니다.",
            # 강조 정규화와 함께 — 부정 판정도 정규화된 사본에서 해야 한다.
            "본 평가는 회사채의 **매수**를 *추천*하는 것이 **아니다**.",
        ],
    )
    def test_disclaimer_forms_pass(self, text: str) -> None:
        assert find_investment_advice(text) is None

    def test_negation_in_a_later_sentence_does_not_excuse_advice(self) -> None:
        assert find_investment_advice("매수를 권합니다. 이는 확정 사항이 아닙니다.") is not None

    def test_emphasized_advice_is_still_caught(self) -> None:
        assert find_investment_advice("**매수하는 것을** 추천합니다.") is not None
