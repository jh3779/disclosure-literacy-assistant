"""조회 기록 메모리 저장소의 순서·상한·스냅샷 경계."""

from disclosure_lit.factcheck.verify import FactCheckItem, FactCheckResult
from disclosure_lit.report.card import ReportCard


def _result(text: str):
    from disclosure_lit.demo.history import LookupResult, MeaningSnapshot

    return LookupResult(
        text=text,
        corp_code="00126380",
        corp_name="삼성전자",
        card=ReportCard(
            fact_check=FactCheckResult(
                claim=text,
                items=[FactCheckItem(fact=text, supporting_section=None, polarity="positive")],
            ),
            phrase_stats=None,
        ),
        is_sample=False,
        meaning=MeaningSnapshot(reason="조회 당시 맥락 안내"),
    )


def test_history_keeps_latest_entries_and_gives_repeat_queries_distinct_ids():
    from disclosure_lit.demo.history import ResultHistory

    history = ResultHistory(limit=2)
    first = history.add(_result("동일 문장"))
    second = history.add(_result("동일 문장"))
    third = history.add(_result("새 문장"))
    assert len({first.record_id, second.record_id, third.record_id}) == 3
    assert [e.record_id for e in history.entries()] == [third.record_id, second.record_id]
    assert first.created_at.tzinfo is not None


def test_history_snapshots_are_independent_of_source_and_read_mutations():
    from disclosure_lit.demo.history import ResultHistory

    history = ResultHistory()
    result = _result("보관할 원본")
    history.add(result)
    result.card.fact_check.items.clear()
    saved = history.entries()[0]
    assert saved.result.card.fact_check.items[0].fact == "보관할 원본"
    saved.result.card.fact_check.items.clear()
    assert history.entries()[0].result.card.fact_check.items[0].fact == "보관할 원본"
    assert saved.result.meaning.reason == "조회 당시 맥락 안내"
