"""L5 — run_eval.py 오케스트레이션 단위 테스트.

verify_claim/judge_recall/judge_hallucination(전부 LLM 호출)은 monkeypatch로 대체해
"gold 사례 → 검색 → 검증 → 채점 → 집계" 배선 로직만 검증한다(tests/test_pipeline.py와
동일 원칙).
"""

from __future__ import annotations

import numpy as np
import pytest

import disclosure_lit.eval.run_eval as run_eval_module
import disclosure_lit.retrieval.company_index as company_index_module
from disclosure_lit.dart.ingest import DisclosureSection
from disclosure_lit.eval.gold_schema import RumorGoldCase
from disclosure_lit.eval.judges import HallucinationVerdict, RecallVerdict
from disclosure_lit.eval.run_eval import (
    CaseResult,
    build_company_index,
    evaluate_all,
    evaluate_case,
    summarize,
)
from disclosure_lit.factcheck.verify import FactCheckItem, FactCheckResult
from disclosure_lit.retrieval.company_index import CompanyDisclosureIndex


@pytest.fixture(autouse=True)
def _mock_embeddings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        company_index_module,
        "embed_texts",
        lambda texts: np.zeros((len(texts), 4), dtype="float32"),
    )
    monkeypatch.setattr(
        company_index_module, "embed_query", lambda text: np.zeros(4, dtype="float32")
    )


def _gold_case(**overrides: object) -> RumorGoldCase:
    defaults = {
        "rumor_text": "테스트 루머",
        "corp_code": "00164779",
        "expected_facts": ["기대 사실1", "기대 사실2"],
        "source_url": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=1",
    }
    defaults.update(overrides)
    return RumorGoldCase(**defaults)  # type: ignore[arg-type]


def _section() -> DisclosureSection:
    return DisclosureSection(
        rcept_no="R1", corp_code="00164779", form_type="유상증자", section_title="제목", text="본문"
    )


class TestEvaluateCase:
    def test_abstention_is_counted_as_zero_recall_not_a_missing_case(self, monkeypatch):
        monkeypatch.setattr(
            run_eval_module,
            "verify_claim",
            lambda *a, **k: FactCheckResult(
                claim="루머", items=[], status="abstained", reason="low_relevance"
            ),
        )
        monkeypatch.setattr(
            run_eval_module,
            "judge_recall",
            lambda expected, actual: RecallVerdict(covered=[False, False]),
        )
        result = evaluate_case(_gold_case(), company_index=CompanyDisclosureIndex())
        assert result.abstained
        assert result.abstention_reason == "low_relevance"
        assert summarize([result])["abstention_rate"] == 1.0
        assert result.recall == 0.0
        assert result.hallucination_rate == 0.0

    def test_engine_failure_is_not_scored_as_successful_abstention(self, monkeypatch):
        monkeypatch.setattr(
            run_eval_module,
            "verify_claim",
            lambda *a, **k: FactCheckResult(
                claim="루머", items=[], status="error", reason="guard_failed"
            ),
        )
        monkeypatch.setattr(
            run_eval_module,
            "judge_recall",
            lambda expected, actual: RecallVerdict(covered=[False, False]),
        )
        result = evaluate_case(_gold_case(), company_index=CompanyDisclosureIndex())
        assert result.failed
        assert result.failure_reason == "guard_failed"
        assert not result.abstained  # 기권(정상 동작)과 실패(엔진 고장)는 다른 사건이다
        assert summarize([result])["failure_rate"] == 1.0

    def test_engine_failure_stays_in_the_denominator(self, monkeypatch):
        """엔진 실패 사례를 결과에서 빼면 재현율이 조용히 올라간다 — 빼지 않는다.

        예외로 올리면 `evaluate_all()`의 케이스 단위 실패 격리가 그 사례를 결과
        목록에서 통째로 제거한다. 하필 재현율 0점짜리만 분모에서 빠지므로 평균이
        실제보다 좋아 보인다(수정 전 실측: 4건 중 1건 실패 시 75.0% -> 100.0%).
        """
        monkeypatch.setattr(
            run_eval_module,
            "verify_claim",
            lambda *a, **k: FactCheckResult(
                claim="루머", items=[], status="error", reason="extraction_failed"
            ),
        )
        monkeypatch.setattr(
            run_eval_module,
            "judge_recall",
            lambda expected, actual: RecallVerdict(covered=[False, False]),
        )
        monkeypatch.setattr(
            run_eval_module,
            "judge_hallucination",
            lambda claim, actual: HallucinationVerdict(unsupported=[]),
        )
        results = evaluate_all([_gold_case()], company_index=CompanyDisclosureIndex())
        assert len(results) == 1  # 결과 목록에서 사라지지 않는다
        assert summarize(results)["mean_recall"] == 0.0

    def test_computes_recall_and_hallucination_from_judges(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        index = CompanyDisclosureIndex()
        index.index([_section()])

        monkeypatch.setattr(
            run_eval_module,
            "verify_claim",
            lambda keyword, sections: FactCheckResult(
                claim=keyword,
                items=[
                    FactCheckItem(fact="실제 사실1", supporting_section=None, polarity="positive")
                ],
            ),
        )
        monkeypatch.setattr(
            run_eval_module,
            "judge_recall",
            lambda expected, actual: RecallVerdict(covered=[True, False]),
        )
        monkeypatch.setattr(
            run_eval_module,
            "judge_hallucination",
            lambda expected, actual: HallucinationVerdict(fabricated=[False]),
        )

        result = evaluate_case(_gold_case(), company_index=index)

        assert result.n_actual_facts == 1
        assert result.recall == 0.5
        assert result.hallucination_rate == 0.0

    def test_empty_company_index_still_produces_a_result(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """코퍼스가 없어도(수집 전) 에러 없이 recall=0으로 채점된다."""
        index = CompanyDisclosureIndex()  # 빈 인덱스

        monkeypatch.setattr(
            run_eval_module,
            "verify_claim",
            lambda keyword, sections: FactCheckResult(claim=keyword, items=[]),
        )
        monkeypatch.setattr(
            run_eval_module,
            "judge_recall",
            lambda expected, actual: RecallVerdict(covered=[False, False]),
        )
        monkeypatch.setattr(
            run_eval_module,
            "judge_hallucination",
            lambda expected, actual: HallucinationVerdict(fabricated=[]),
        )

        result = evaluate_case(_gold_case(), company_index=index)

        assert result.n_actual_facts == 0
        assert result.recall == 0.0
        assert result.hallucination_rate == 0.0


class TestBuildCompanyIndex:
    def test_missing_raw_dir_returns_empty_index(self, tmp_path) -> None:
        index = build_company_index(tmp_path / "없음")
        assert index.search("00164779", "") == []

    def test_corp_codes_filter_indexes_only_requested_companies(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """2026-09-22 — 골드셋에 안 나오는 회사까지 임베딩하면 낭비다(실측: 전체
        114개사 40분대 vs 골드셋 14개사 7분). `search()`가 어차피 corp_code로
        좁히므로 채점 결과는 그대로다."""
        sections = [
            DisclosureSection(
                rcept_no="R1",
                corp_code="C_골드",
                form_type="기타",
                section_title="제목",
                text="본문",
            ),
            DisclosureSection(
                rcept_no="R2",
                corp_code="C_무관",
                form_type="기타",
                section_title="제목",
                text="본문",
            ),
        ]
        monkeypatch.setattr(run_eval_module, "load_all_local_sections", lambda _: sections)

        index = build_company_index(tmp_path, corp_codes={"C_골드"})

        assert [s.rcept_no for s in index.search("C_골드", "")] == ["R1"]
        assert index.search("C_무관", "") == []  # 인덱싱 자체가 안 됐다

    def test_without_corp_codes_indexes_everything(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """기본값(None)은 예전과 동일하게 전부 인덱싱한다 — 하위 호환."""
        sections = [
            DisclosureSection(
                rcept_no="R1", corp_code="C1", form_type="기타", section_title="제목", text="본문"
            ),
            DisclosureSection(
                rcept_no="R2", corp_code="C2", form_type="기타", section_title="제목", text="본문"
            ),
        ]
        monkeypatch.setattr(run_eval_module, "load_all_local_sections", lambda _: sections)

        index = build_company_index(tmp_path)

        assert index.search("C1", "") != []
        assert index.search("C2", "") != []


class TestEvaluateAllErrorIsolation:
    def test_one_failing_case_does_not_lose_other_results(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """한 케이스의 LLM 호출이 실패해도 나머지 채점 결과는 유실되지 않는다(PR #53
        리뷰 지적, build_corpus.py 종목 단위 실패 격리와 동일 원칙)."""
        index = CompanyDisclosureIndex()

        def _flaky_verify_claim(keyword, sections):
            if keyword == "실패 케이스":
                raise TimeoutError("LLM 호출 타임아웃")
            return FactCheckResult(claim=keyword, items=[])

        monkeypatch.setattr(run_eval_module, "verify_claim", _flaky_verify_claim)
        monkeypatch.setattr(
            run_eval_module, "judge_recall", lambda expected, actual: RecallVerdict(covered=[True])
        )
        monkeypatch.setattr(
            run_eval_module,
            "judge_hallucination",
            lambda expected, actual: HallucinationVerdict(fabricated=[]),
        )
        cases = [
            _gold_case(rumor_text="정상 케이스1"),
            _gold_case(rumor_text="실패 케이스"),
            _gold_case(rumor_text="정상 케이스2"),
        ]

        results = evaluate_all(cases, company_index=index)

        assert len(results) == 2
        assert {r.rumor_text for r in results} == {"정상 케이스1", "정상 케이스2"}

    def test_all_cases_failing_returns_empty_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        index = CompanyDisclosureIndex()

        def _always_fail(keyword, sections):
            raise RuntimeError("항상 실패")

        monkeypatch.setattr(run_eval_module, "verify_claim", _always_fail)
        cases = [_gold_case(rumor_text="A"), _gold_case(rumor_text="B")]

        results = evaluate_all(cases, company_index=index)

        assert results == []


class TestSummarize:
    def test_empty_results(self) -> None:
        summary = summarize([])
        assert summary == {
            "n": 0,
            "mean_recall": 0.0,
            "mean_hallucination_rate": 0.0,
            "n_dropped_advice": 0,
            "abstention_rate": 0.0,
            "failure_rate": 0.0,
        }

    def test_averages_across_cases(self, monkeypatch: pytest.MonkeyPatch) -> None:
        index = CompanyDisclosureIndex()
        monkeypatch.setattr(
            run_eval_module,
            "verify_claim",
            lambda keyword, sections: FactCheckResult(claim=keyword, items=[]),
        )
        monkeypatch.setattr(
            run_eval_module,
            "judge_recall",
            lambda expected, actual: RecallVerdict(covered=[True]),
        )
        monkeypatch.setattr(
            run_eval_module,
            "judge_hallucination",
            lambda expected, actual: HallucinationVerdict(fabricated=[]),
        )
        cases = [_gold_case(rumor_text="A"), _gold_case(rumor_text="B")]

        results = evaluate_all(cases, company_index=index)
        summary = summarize(results)

        assert summary["n"] == 2
        assert summary["mean_recall"] == 1.0
        assert summary["mean_hallucination_rate"] == 0.0


class TestDroppedAdviceIsSurfaced:
    """2026-09-22 PR #115 리뷰(@9abin) — recall이 낮을 때 "검색이 근거를 못 찾은 것"인지
    "compliance 필터가 지운 것"인지 구분할 수 있어야 한다. 필터가 버린 개수를 결과에
    싣고 합계까지 요약에 올린다(필드만 두고 안 보여주면 아무도 모른다)."""

    def test_case_result_carries_dropped_count(self, monkeypatch: pytest.MonkeyPatch) -> None:
        index = CompanyDisclosureIndex()
        monkeypatch.setattr(
            run_eval_module,
            "verify_claim",
            lambda keyword, sections: FactCheckResult(claim=keyword, items=[], n_dropped_advice=2),
        )
        monkeypatch.setattr(
            run_eval_module, "judge_recall", lambda expected, actual: RecallVerdict(covered=[False])
        )
        monkeypatch.setattr(
            run_eval_module,
            "judge_hallucination",
            lambda expected, actual: HallucinationVerdict(fabricated=[]),
        )

        result = evaluate_case(_gold_case(), company_index=index)

        assert result.n_dropped_advice == 2

    def test_summary_totals_dropped_across_cases(self) -> None:
        results = [
            CaseResult(
                rumor_text="A",
                corp_code="C1",
                n_actual_facts=1,
                recall=1.0,
                hallucination_rate=0.0,
                n_dropped_advice=1,
            ),
            CaseResult(
                rumor_text="B",
                corp_code="C2",
                n_actual_facts=1,
                recall=1.0,
                hallucination_rate=0.0,
                n_dropped_advice=3,
            ),
        ]

        assert summarize(results)["n_dropped_advice"] == 4
