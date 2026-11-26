"""factcheck/verify.py::verify_claim 단위 테스트. LLM 호출 없음(monkeypatch)."""

from __future__ import annotations

import pytest

import disclosure_lit.factcheck.verify as verify_module
from disclosure_lit.dart.ingest import DisclosureSection
from disclosure_lit.factcheck.verify import (
    MAX_SECTION_CHARS,
    FactCheckItem,
    FactCheckResult,
    _FactCandidate,
    _FactExtraction,
    _GroundednessCheck,
    _render_sections,
    verify_claim,
)
from disclosure_lit.llm import LLMResult


def _section(text: str = "본문", title: str = "제목") -> DisclosureSection:
    return DisclosureSection(
        rcept_no="R1", corp_code="00126380", form_type="유상증자", section_title=title, text=text
    )


def _stub_complete(draft: _FactExtraction | None, guard: _GroundednessCheck | None):
    """1차(draft, schema=_FactExtraction) / 2차(guard, schema=_GroundednessCheck) 호출을
    schema 종류로 구분해 각각 다른 응답을 돌려준다."""
    calls: list[str] = []

    def _complete(system: str, user: str, *, tier: str, schema) -> LLMResult:
        if schema is _FactExtraction:
            calls.append("draft")
            return LLMResult(text="", parsed=draft, input_tokens=0, output_tokens=0)
        calls.append("guard")
        return LLMResult(text="", parsed=guard, input_tokens=0, output_tokens=0)

    _complete.calls = calls  # type: ignore[attr-defined]
    return _complete


class TestVerifyClaim:
    def test_low_relevance_abstains_without_calling_llm(self, monkeypatch):
        stub = _stub_complete(None, None)
        monkeypatch.setattr(verify_module, "complete", stub)
        result = verify_claim("루머", [_section()], relevance_scores=[0.2], min_relevance_score=0.5)
        assert result.abstained
        assert result.reason == "low_relevance"
        assert not result.items
        assert stub.calls == []

    def test_threshold_boundary_keeps_original_evidence_context(self, monkeypatch):
        sections = [_section("첫 번째"), _section("두 번째")]
        stub = _stub_complete(
            _FactExtraction(
                items=[_FactCandidate(fact="근거", section_index=1, polarity="negative")]
            ),
            _GroundednessCheck(grounded_indices=[0]),
        )
        monkeypatch.setattr(verify_module, "complete", stub)
        result = verify_claim(
            "루머", sections, relevance_scores=[0.5, 0.1], min_relevance_score=0.5
        )
        assert result.status == "ok"
        assert result.items[0].supporting_section is sections[1]
        assert stub.calls == ["draft", "guard"]

    @pytest.mark.parametrize(
        "scores, threshold",
        [
            (None, 0.5),
            ([], 0.5),
            ([float("nan")], 0.5),
            ([float("inf")], 0.5),
            ([0.5], float("nan")),
        ],
    )
    def test_invalid_gate_inputs_fail_before_llm(self, monkeypatch, scores, threshold):
        stub = _stub_complete(None, None)
        monkeypatch.setattr(verify_module, "complete", stub)
        with pytest.raises(ValueError):
            verify_claim(
                "루머", [_section()], relevance_scores=scores, min_relevance_score=threshold
            )
        assert stub.calls == []

    def test_returns_empty_result_without_llm_call_when_no_sections(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stub = _stub_complete(draft=None, guard=None)
        monkeypatch.setattr(verify_module, "complete", stub)

        result = verify_claim("유상증자", [])

        assert result == FactCheckResult(
            claim="유상증자", items=[], status="abstained", reason="no_sections"
        )
        assert stub.calls == []  # LLM 호출 자체를 안 함 — 검색할 게 없으니 비용 낭비 방지

    def test_grounded_facts_pass_through_with_section_and_polarity(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sections = [_section("500억 유상증자를 결정하였습니다.", "신주발행결정")]
        draft = _FactExtraction(
            items=[
                _FactCandidate(
                    fact="500억원 유상증자 결정 사실이 있다.", section_index=0, polarity="positive"
                ),
            ]
        )
        guard = _GroundednessCheck(grounded_indices=[0])
        monkeypatch.setattr(verify_module, "complete", _stub_complete(draft, guard))

        result = verify_claim("유상증자", sections)

        assert result.claim == "유상증자"
        assert result.items == [
            FactCheckItem(
                fact="500억원 유상증자 결정 사실이 있다.",
                supporting_section=sections[0],
                polarity="positive",
            )
        ]

    def test_guard_drops_ungrounded_facts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """draft가 2건을 뽑았지만 guard가 1건만 근거 있다고 확인 — 그것만 남아야 한다."""
        sections = [_section("500억 유상증자를 결정하였습니다.")]
        draft = _FactExtraction(
            items=[
                _FactCandidate(fact="근거 있는 사실", section_index=0, polarity="positive"),
                _FactCandidate(fact="지어낸 사실(환각)", section_index=0, polarity="negative"),
            ]
        )
        guard = _GroundednessCheck(grounded_indices=[0])  # 1번(index 1)은 탈락
        monkeypatch.setattr(verify_module, "complete", _stub_complete(draft, guard))

        result = verify_claim("유상증자", sections)

        assert len(result.items) == 1
        assert result.items[0].fact == "근거 있는 사실"

    def test_positive_and_negative_facts_both_kept_equally(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """긍정/부정 사실을 한쪽만 골라내지 않는다(CLAUDE.md 주의점 2)."""
        sections = [_section()]
        draft = _FactExtraction(
            items=[
                _FactCandidate(fact="뒷받침하는 사실", section_index=0, polarity="positive"),
                _FactCandidate(fact="배치되는 사실", section_index=0, polarity="negative"),
            ]
        )
        guard = _GroundednessCheck(grounded_indices=[0, 1])
        monkeypatch.setattr(verify_module, "complete", _stub_complete(draft, guard))

        result = verify_claim("유상증자", sections)

        polarities = {item.polarity for item in result.items}
        assert polarities == {"positive", "negative"}

    def test_is_confirmed_and_next_update_note_pass_through(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """조회공시 해명처럼 미확정 사실 + 재공시 예정일이 있으면 그대로 전달돼야 한다."""
        sections = [_section("검토 중이며 확정된 사항은 없습니다.", "조회공시 요구에 대한 답변")]
        draft = _FactExtraction(
            items=[
                _FactCandidate(
                    fact="4조 규모 매각을 검토 중이나 확정된 사항은 없다.",
                    section_index=0,
                    polarity="neutral",
                    is_confirmed=False,
                    next_update_note="3개월 이내(2026-12-08) 재공시 예정",
                ),
            ]
        )
        guard = _GroundednessCheck(grounded_indices=[0])
        monkeypatch.setattr(verify_module, "complete", _stub_complete(draft, guard))

        result = verify_claim("매각설", sections)

        assert result.items[0].is_confirmed is False
        assert result.items[0].next_update_note == "3개월 이내(2026-12-08) 재공시 예정"

    def test_is_confirmed_defaults_true_and_next_update_note_defaults_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """LLM이 새 필드를 안 채워도(구버전 프롬프트 등) 안전한 기본값으로 동작한다."""
        sections = [_section("500억 유상증자를 결정하였습니다.")]
        draft = _FactExtraction(
            items=[_FactCandidate(fact="유상증자 결정 사실", section_index=0, polarity="positive")]
        )
        guard = _GroundednessCheck(grounded_indices=[0])
        monkeypatch.setattr(verify_module, "complete", _stub_complete(draft, guard))

        result = verify_claim("유상증자", sections)

        assert result.items[0].is_confirmed is True
        assert result.items[0].next_update_note is None

    def test_no_draft_candidates_skips_guard_call(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """관련 사실이 하나도 없으면 guard까지 갈 필요 없다 — 호출 비용 절약."""
        sections = [_section()]
        draft = _FactExtraction(items=[])
        stub = _stub_complete(draft, guard=_GroundednessCheck(grounded_indices=[]))
        monkeypatch.setattr(verify_module, "complete", stub)

        result = verify_claim("무관한 클레임", sections)

        assert result.items == []
        assert stub.calls == ["draft"]  # guard 호출 안 됨

    def test_draft_unparsed_returns_empty_result(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """1차 LLM 호출이 스키마 파싱에 실패해도 크래시하지 않는다."""
        sections = [_section()]
        monkeypatch.setattr(verify_module, "complete", _stub_complete(draft=None, guard=None))

        result = verify_claim("유상증자", sections)

        assert result == FactCheckResult(
            claim="유상증자", items=[], status="error", reason="extraction_failed"
        )

    def test_guard_unparsed_drops_everything_safely(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """2차(guard) 호출이 실패하면 안전한 쪽(아무것도 통과 안 시킴)으로 폴백한다 —
        검증 안 된 사실을 그대로 내보내는 것보다 낫다."""
        sections = [_section()]
        draft = _FactExtraction(
            items=[_FactCandidate(fact="검증 안 된 사실", section_index=0, polarity="positive")]
        )
        monkeypatch.setattr(verify_module, "complete", _stub_complete(draft, guard=None))

        result = verify_claim("유상증자", sections)

        assert result.items == []
        assert result.status == "error"
        assert result.reason == "guard_failed"
        assert not result.abstained

    def test_section_index_out_of_range_falls_back_to_no_supporting_section(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sections = [_section()]
        draft = _FactExtraction(
            items=[_FactCandidate(fact="일반적 사실", section_index=5, polarity="neutral")]
        )
        guard = _GroundednessCheck(grounded_indices=[0])
        monkeypatch.setattr(verify_module, "complete", _stub_complete(draft, guard))

        result = verify_claim("유상증자", sections)

        assert result.items[0].supporting_section is None

    def test_section_index_none_means_no_supporting_section(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sections = [_section()]
        draft = _FactExtraction(
            items=[
                _FactCandidate(
                    fact="특정 섹션 없이 일반적인 사실", section_index=None, polarity="neutral"
                )
            ]
        )
        guard = _GroundednessCheck(grounded_indices=[0])
        monkeypatch.setattr(verify_module, "complete", _stub_complete(draft, guard))

        result = verify_claim("유상증자", sections)

        assert result.items[0].supporting_section is None


class TestGuardReceivesClaimForRelevanceCheck:
    """이슈 #99 — 무관한 근거 문서가 "참고 사실"로 잘못 노출된 사례. guard 호출이
    클레임을 몰라서 "근거는 있지만 클레임과 무관한" 사실을 걸러낼 방법이 없었다 —
    guard 프롬프트에 클레임이 실제로 전달되는지 배선을 확인한다."""

    def test_guard_prompt_includes_claim_keyword(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sections = [_section("500억 유상증자를 결정하였습니다.")]
        draft = _FactExtraction(
            items=[_FactCandidate(fact="사실", section_index=0, polarity="positive")]
        )
        guard = _GroundednessCheck(grounded_indices=[0])

        seen_guard_user_prompt: list[str] = []

        def _complete(system: str, user: str, *, tier: str, schema):
            if schema is _GroundednessCheck:
                seen_guard_user_prompt.append(user)
                return LLMResult(text="", parsed=guard, input_tokens=0, output_tokens=0)
            return LLMResult(text="", parsed=draft, input_tokens=0, output_tokens=0)

        monkeypatch.setattr(verify_module, "complete", _complete)

        verify_claim("특정 클레임 키워드", sections)

        assert len(seen_guard_user_prompt) == 1
        assert "특정 클레임 키워드" in seen_guard_user_prompt[0]


class TestRenderSectionsTruncation:
    """Issue #75 — 재무제표류 거대 섹션이 프롬프트에 그대로 들어가 OpenAI TPM 한도를
    초과하던 문제(gold set 27건 중 7건 실패 재현)의 방어선. _render_sections()가
    프롬프트에 넣는 텍스트 자체에 길이 상한을 거는지 검증한다."""

    def test_short_section_text_is_unchanged(self) -> None:
        sections = [_section("500억 유상증자를 결정하였습니다.")]

        rendered = _render_sections(sections)

        assert "500억 유상증자를 결정하였습니다." in rendered

    def test_long_section_text_is_truncated_in_prompt(self) -> None:
        """실측(Issue #75) — 재무제표 섹션처럼 69만자짜리도 나올 수 있다. 여기선
        MAX_SECTION_CHARS를 훌쩍 넘는 텍스트로 축소 재현한다."""
        huge_text = "재무제표 숫자 " * (MAX_SECTION_CHARS // 5)  # MAX_SECTION_CHARS 훨씬 초과
        sections = [_section(huge_text)]

        rendered = _render_sections(sections)

        # 렌더링된 프롬프트 안의 본문 부분은 상한을 넘지 않는다("[0] 제목\n" 등 오버헤드 제외).
        body = rendered.split("\n", 1)[1]
        assert len(body) <= MAX_SECTION_CHARS + len(" …(이하 생략)")
        assert len(huge_text) > MAX_SECTION_CHARS  # 원본은 실제로 상한보다 길었음을 확인

    def test_truncation_does_not_mutate_original_section_text(self) -> None:
        """프롬프트용으로만 자르고, DisclosureSection.text 원본과 supporting_section으로
        반환되는 객체는 그대로 둔다(embed.py::MAX_EMBED_CHARS와 같은 원칙)."""
        huge_text = "가" * (MAX_SECTION_CHARS + 1000)
        sections = [_section(huge_text)]

        _render_sections(sections)

        assert sections[0].text == huge_text  # 원본 섹션 객체는 변형되지 않았다

    def test_multiple_long_sections_each_independently_capped(self) -> None:
        sections = [
            _section("가" * (MAX_SECTION_CHARS * 2), title="A"),
            _section("나" * (MAX_SECTION_CHARS * 2), title="B"),
        ]

        rendered = _render_sections(sections)

        for marker in ("[0] A", "[1] B"):
            assert marker in rendered
        # 두 섹션 다 원문(상한의 2배) 그대로는 안 들어갔다.
        assert "가" * (MAX_SECTION_CHARS * 2) not in rendered
        assert "나" * (MAX_SECTION_CHARS * 2) not in rendered


class TestVerifyClaimUsesTruncatedPrompt:
    """verify_claim()이 실제로 _render_sections()를 거친(즉 잘린) 텍스트를 LLM
    프롬프트로 보내는지 — 위 TestRenderSectionsTruncation과 별개로 verify_claim()
    자체의 배선을 확인한다."""

    def test_huge_section_text_kept_out_of_llm_prompt_but_kept_in_supporting_section(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        huge_text = "재무제표 숫자 " * (MAX_SECTION_CHARS // 5)
        sections = [_section(huge_text, title="재무에 관한 사항")]
        draft = _FactExtraction(
            items=[_FactCandidate(fact="사실", section_index=0, polarity="neutral")]
        )
        guard = _GroundednessCheck(grounded_indices=[0])

        seen_prompts: list[str] = []

        def _complete(system: str, user: str, *, tier: str, schema):
            seen_prompts.append(user)
            parsed = draft if schema is _FactExtraction else guard
            return LLMResult(text="", parsed=parsed, input_tokens=0, output_tokens=0)

        monkeypatch.setattr(verify_module, "complete", _complete)

        result = verify_claim("유상증자", sections)

        # LLM에 보낸 두 프롬프트(draft/guard) 다 원문 전체를 그대로 담지 않았다.
        assert len(seen_prompts) == 2
        for prompt in seen_prompts:
            assert huge_text not in prompt
        # 그래도 반환되는 supporting_section은 원본(잘리지 않은) 섹션 그대로다.
        assert result.items[0].supporting_section is sections[0]
        assert result.items[0].supporting_section.text == huge_text
