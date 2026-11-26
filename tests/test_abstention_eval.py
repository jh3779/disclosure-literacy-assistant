"""반복 평가에서 실패를 기권으로 숨기거나 두 버전의 후보가 달라지지 않게 한다."""

import json

import pytest

from disclosure_lit.dart.ingest import DisclosureSection
from disclosure_lit.eval import abstention
from disclosure_lit.eval.gold_schema import RumorGoldCase
from disclosure_lit.eval.run_eval import CaseResult
from disclosure_lit.retrieval.company_index import SearchHit


def test_prepared_search_preserves_original_search_cutoff():
    section = DisclosureSection(
        rcept_no="R1", corp_code="C1", form_type="공시", section_title="제목", text="본문"
    )
    hits = [SearchHit(section, 0.7), SearchHit(section, 0.1)]
    prepared = abstention.PreparedSearch({("C1", "루머"): hits})
    assert prepared.search_with_scores("C1", "루머") == hits[:1]
    assert prepared.search("C1", "루머") == [section]
    assert prepared.hits["C1", "루머"] == hits


def test_repeated_comparison_records_failures_and_preserves_profiles(monkeypatch, tmp_path):
    case = RumorGoldCase(
        rumor_text="루머", corp_code="C1", expected_facts=["사실"], source_url="https://example.com"
    )
    section = DisclosureSection(
        rcept_no="R1", corp_code="C1", form_type="공시", section_title="제목", text="본문"
    )

    class Index:
        def search_with_scores(self, *args, **kwargs):
            return [SearchHit(section, 0.4)]

    def evaluate(case, *, company_index, min_relevance_score):
        assert company_index.search(case.corp_code, case.rumor_text) == [section]
        if min_relevance_score is None:
            raise TimeoutError("private provider detail")
        return CaseResult(
            "루머", "C1", 0, 0.0, 0.0, abstained=True, abstention_reason="low_relevance"
        )

    monkeypatch.setattr(abstention, "load_gold_cases", lambda path: [case])
    monkeypatch.setattr(abstention, "load_all_local_sections", lambda path: [section])
    monkeypatch.setattr(abstention, "load_company_index", lambda *args: Index())
    monkeypatch.setattr(abstention, "evaluate_case", evaluate)
    gold = tmp_path / "gold.jsonl"
    gold.write_text("gold", encoding="utf-8")
    output = tmp_path / "report.json"
    report = abstention.run_experiment(
        gold_path=gold, raw_dir=tmp_path, output=output, threshold=0.5, runs=2, workers=2
    )
    assert len(report["runs"]) == 4
    assert report == json.loads(output.read_text(encoding="utf-8"))
    assert report["profiles"][0]["hits"] == [{"score": 0.4, "rcept_no": "R1"}]
    for run in report["runs"]:
        if run["variant"] == "baseline":
            assert not run["complete"]
            assert run["n_failed"] == 1
            assert run["cases"] == [{"case_index": 0, "error": "TimeoutError"}]
        else:
            assert run["complete"]
            assert run["summary"]["abstention_rate"] == 1.0


@pytest.mark.parametrize("threshold", [float("nan"), float("inf")])
def test_experiment_rejects_invalid_threshold_before_loading_data(tmp_path, threshold):
    with pytest.raises(ValueError, match="finite"):
        abstention.run_experiment(
            gold_path=tmp_path / "missing",
            raw_dir=tmp_path,
            output=tmp_path / "out",
            threshold=threshold,
        )
