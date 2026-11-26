"""L5 — judge_recall/judge_hallucination 단위 테스트. LLM 호출 없음(monkeypatch)."""

from __future__ import annotations

import pytest

import disclosure_lit.eval.judges as judges_module
from disclosure_lit.eval.judges import (
    HallucinationVerdict,
    RecallVerdict,
    judge_hallucination,
    judge_recall,
)
from disclosure_lit.llm import LLMResult


def _stub_complete(parsed):
    def _complete(system: str, user: str, *, tier: str, schema):
        return LLMResult(text="", parsed=parsed, input_tokens=0, output_tokens=0)

    return _complete


class TestJudgeRecall:
    def test_empty_expected_facts_is_full_recall(self) -> None:
        verdict = judge_recall([], ["아무 사실"])
        assert verdict.recall == 1.0

    def test_recall_computed_from_covered_flags(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            judges_module, "complete", _stub_complete(RecallVerdict(covered=[True, False]))
        )
        verdict = judge_recall(["기대1", "기대2"], ["실제1"])
        assert verdict.recall == 0.5

    def test_pads_short_response(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """모델이 기대 사실 개수보다 적게 응답해도 죽지 않고 나머지는 False로 채운다."""
        monkeypatch.setattr(
            judges_module, "complete", _stub_complete(RecallVerdict(covered=[True]))
        )
        verdict = judge_recall(["기대1", "기대2", "기대3"], ["실제1"])
        assert verdict.covered == [True, False, False]

    def test_none_parsed_falls_back_to_all_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(judges_module, "complete", _stub_complete(None))
        verdict = judge_recall(["기대1", "기대2"], [])
        assert verdict.recall == 0.0


class TestJudgeHallucination:
    def test_no_actual_facts_is_zero_hallucination(self) -> None:
        verdict = judge_hallucination(["기대1"], [])
        assert verdict.hallucination_rate == 0.0

    def test_hallucination_rate_computed_from_fabricated_flags(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            judges_module,
            "complete",
            _stub_complete(HallucinationVerdict(fabricated=[False, True])),
        )
        verdict = judge_hallucination(["기대1"], ["근거있는 사실", "지어낸 사실"])
        assert verdict.hallucination_rate == 0.5

    def test_pads_short_response(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            judges_module, "complete", _stub_complete(HallucinationVerdict(fabricated=[True]))
        )
        verdict = judge_hallucination(["기대1"], ["실제1", "실제2"])
        assert verdict.fabricated == [True, False]
