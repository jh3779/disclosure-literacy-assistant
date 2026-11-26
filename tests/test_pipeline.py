"""pipeline.py 단위 테스트 — Issue #32/DEC-005 배선 로직.

B(김가빈)의 실제 엔진(verify_claim/compute_stats/PhraseClusterIndex.match)은 아직
NotImplementedError 스텁이라, 여기서는 monkeypatch로 대체해 오케스트레이션 로직만
검증한다(어떤 인자로 몇 번 불렀는지).
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

import disclosure_lit.retrieval.company_index as company_index_module
from disclosure_lit import pipeline
from disclosure_lit.dart.client import CorpCodeEntry, CorpCodeIndex
from disclosure_lit.dart.ingest import DisclosureSection
from disclosure_lit.dictionary.stats import ClusterStats
from disclosure_lit.factcheck.claim import Claim
from disclosure_lit.factcheck.verify import FactCheckResult
from disclosure_lit.retrieval.company_index import CompanyDisclosureIndex
from disclosure_lit.retrieval.phrase_index import PhraseCluster


@pytest.fixture(autouse=True)
def _mock_embeddings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        pipeline, "settings", replace(pipeline.settings, factcheck_min_relevance_score=None)
    )
    # CompanyDisclosureIndex.index()/search() 가 PR #35부터 실제 임베딩을 쓴다 —
    # sentence-transformers(무거운 optional extra)는 CI 기본 실행에 없으므로
    # 배선 로직만 보는 이 테스트에서는 결정론적 가짜 벡터로 대체한다
    # (tests/test_company_disclosure_index.py와 같은 원칙).
    monkeypatch.setattr(
        company_index_module,
        "embed_texts",
        lambda texts: np.zeros((len(texts), 4), dtype="float32"),
    )
    monkeypatch.setattr(
        company_index_module, "embed_query", lambda text: np.zeros(4, dtype="float32")
    )


def test_enabled_gate_skips_extraction_before_llm(monkeypatch):
    import disclosure_lit.factcheck.verify as verify_module

    monkeypatch.setattr(
        pipeline, "settings", replace(pipeline.settings, factcheck_min_relevance_score=1.1)
    )
    monkeypatch.setattr(verify_module, "complete", lambda *a, **k: pytest.fail("unexpected LLM"))
    index = CompanyDisclosureIndex()
    index.index([_section("R1")])  # 키워드 bonus = 1.0, mock cosine = 0.0
    result = pipeline.run_factcheck(
        _claim(corp_code="00126380"), company_index=index, corp_code_index=CorpCodeIndex([])
    )
    assert result.abstained
    assert result.reason == "low_relevance"


def test_empty_keyword_is_not_reported_as_low_relevance(monkeypatch):
    """검색어가 없으면 비교할 점수 자체가 없다 — 관련성 부족이라고 말하면 거짓말이다.

    `search_with_scores()`는 검색어가 비면 최신순으로 돌려주며 score=None을 채운다.
    문턱이 켜져 있다는 이유만으로 기권해 버리면, 문턱을 끈 경우와 결과의 유무가
    달라지고 사용자에게는 틀린 사유가 표시된다.
    """
    monkeypatch.setattr(
        pipeline, "settings", replace(pipeline.settings, factcheck_min_relevance_score=0.5)
    )
    seen: list[dict] = []

    def _fake_verify(keyword, sections, **kwargs):
        seen.append({"n_sections": len(sections), **kwargs})
        return FactCheckResult(claim=keyword, items=[])

    monkeypatch.setattr(pipeline, "verify_claim", _fake_verify)
    index = CompanyDisclosureIndex()
    index.index([_section("R1")])
    result = pipeline.run_factcheck(
        _claim(corp_code="00126380", keyword=""),
        company_index=index,
        corp_code_index=CorpCodeIndex([]),
    )
    assert result.reason != "low_relevance"
    # 문턱을 끈 경로와 똑같이 섹션을 넘긴다 — 점수 인자는 붙이지 않는다.
    assert seen == [{"n_sections": 1}]


def _claim(
    *, corp_code: str | None = None, corp_name: str | None = None, keyword: str = "유상증자"
) -> Claim:
    return Claim(
        corp_name=corp_name, corp_code=corp_code, keyword=keyword, raw_text=f"{keyword} 루머"
    )


def _section(
    rcept_no: str, corp_code: str = "00126380", *, rcept_dt: str = "20260101"
) -> DisclosureSection:
    return DisclosureSection(
        rcept_no=rcept_no,
        corp_code=corp_code,
        form_type="유상증자",
        section_title="유상증자 결정",
        text="본문",
        rcept_dt=rcept_dt,
    )


class TestResolveCorpCode:
    def test_returns_claim_corp_code_unchanged_when_present(self) -> None:
        claim = _claim(corp_code="00126380", corp_name="아무회사")
        index = CorpCodeIndex([])  # 조회 안 가고 바로 반환돼야 함
        assert pipeline.resolve_corp_code(claim, index) == "00126380"

    def test_looks_up_by_corp_name_when_corp_code_missing(self) -> None:
        claim = _claim(corp_name="삼성전자")
        index = CorpCodeIndex([CorpCodeEntry("00126380", "삼성전자", "005930", "20240101")])
        assert pipeline.resolve_corp_code(claim, index) == "00126380"

    def test_returns_none_when_name_not_found(self) -> None:
        claim = _claim(corp_name="존재하지않는회사")
        index = CorpCodeIndex([CorpCodeEntry("00126380", "삼성전자", "005930", "20240101")])
        assert pipeline.resolve_corp_code(claim, index) is None

    def test_returns_none_when_neither_corp_code_nor_corp_name(self) -> None:
        claim = _claim()
        assert pipeline.resolve_corp_code(claim, CorpCodeIndex([])) is None

    def test_strips_whitespace_before_lookup(self) -> None:
        """리뷰 지적(PR #34, @powercodingman): find_by_name은 정규화 없는 exact-match라
        extract_claim(LLM)이 앞뒤 공백을 붙여 뽑으면 매칭이 실패할 수 있었다."""
        claim = _claim(corp_name="  삼성전자  ")
        index = CorpCodeIndex([CorpCodeEntry("00126380", "삼성전자", "005930", "20240101")])
        assert pipeline.resolve_corp_code(claim, index) == "00126380"


class TestRunFactcheck:
    def test_returns_empty_result_when_corp_code_unresolved(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        called = False

        def fail_verify_claim(*args: object, **kwargs: object) -> FactCheckResult:
            nonlocal called
            called = True
            raise AssertionError("corp_code 미해결 시 verify_claim 을 부르면 안 된다")

        monkeypatch.setattr(pipeline, "verify_claim", fail_verify_claim)

        claim = _claim(corp_name="존재하지않는회사")
        result = pipeline.run_factcheck(
            claim, company_index=CompanyDisclosureIndex(), corp_code_index=CorpCodeIndex([])
        )

        assert called is False
        assert result == FactCheckResult(
            claim=claim.raw_text, items=[], status="error", reason="unresolved_company"
        )

    def test_passes_top_5_of_10_search_results_to_verify_claim(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, object] = {}

        def fake_verify_claim(
            claim_keyword: str, sections: list[DisclosureSection]
        ) -> FactCheckResult:
            captured["claim_keyword"] = claim_keyword
            captured["sections"] = sections
            return FactCheckResult(claim=claim_keyword, items=[])

        monkeypatch.setattr(pipeline, "verify_claim", fake_verify_claim)

        company_index = CompanyDisclosureIndex()
        # 최신순 정렬 후 상위 5개만 verify_claim 에 가야 하므로, 10건을 넣고 최신 5건의
        # rcept_no 를 미리 계산해둔다.
        sections = [_section(f"R{i}", rcept_dt=f"2026{i + 1:02d}01") for i in range(10)]
        company_index.index(sections)
        expected_top5 = sorted(sections, key=lambda s: s.rcept_dt, reverse=True)[:5]

        claim = _claim(corp_code="00126380", keyword="유상증자")
        pipeline.run_factcheck(
            claim, company_index=company_index, corp_code_index=CorpCodeIndex([])
        )

        assert captured["claim_keyword"] == "유상증자"
        assert captured["sections"] == expected_top5
        assert len(captured["sections"]) == 5

    def test_resolves_corp_code_from_corp_name_before_searching(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            pipeline, "verify_claim", lambda kw, sections: FactCheckResult(claim=kw, items=[])
        )

        company_index = CompanyDisclosureIndex()
        company_index.index([_section("R1", "00126380")])
        corp_code_index = CorpCodeIndex(
            [CorpCodeEntry("00126380", "삼성전자", "005930", "20240101")]
        )

        claim = _claim(corp_name="삼성전자", keyword="유상증자")
        result = pipeline.run_factcheck(
            claim, company_index=company_index, corp_code_index=corp_code_index
        )

        assert result.claim == "유상증자"


class TestRunDictionaryLookup:
    def test_returns_none_when_no_match(self, monkeypatch: pytest.MonkeyPatch) -> None:
        called = False

        def fail_compute_stats(*args: object, **kwargs: object) -> ClusterStats:
            nonlocal called
            called = True
            raise AssertionError("매칭 실패 시 compute_stats 를 부르면 안 된다")

        monkeypatch.setattr(pipeline, "compute_stats", fail_compute_stats)

        class _NoMatchIndex:
            def match(self, phrase: str) -> PhraseCluster | None:
                return None

        assert pipeline.run_dictionary_lookup("아무 문구", phrase_index=_NoMatchIndex()) is None
        assert called is False

    def test_returns_compute_stats_result_when_matched(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cluster = PhraseCluster(
            cluster_id="C1", representative_phrase="선제적 재무구조 개선", sample_count=5
        )
        stats = ClusterStats(
            cluster_id="C1",
            sample_count=5,
            outcome_label="유상증자",
            outcome_count=3,
            is_significant=True,
        )

        def fake_compute_stats(cluster_id: str) -> ClusterStats:
            assert cluster_id == "C1"
            return stats

        monkeypatch.setattr(pipeline, "compute_stats", fake_compute_stats)

        class _MatchIndex:
            def match(self, phrase: str) -> PhraseCluster | None:
                return cluster

        assert (
            pipeline.run_dictionary_lookup("선제적 재무구조 개선", phrase_index=_MatchIndex())
            == stats
        )


class TestFactcheckFallbackIsObservable:
    """2026-09-24 구조 감사 후속 — 조용한 폴백에 기록을 남긴다.

    `run_factcheck()`는 corp_code를 해석 못 하면 빈 `FactCheckResult`를 돌려준다.
    화면에는 이것이 "관련 공시 없음"과 똑같이 보이므로, 로그가 없으면 **종목 해석 실패와
    진짜 공시 없음을 사후에 구분할 수 없다**(HANDOFF의 미해결 항목과 같은 뿌리).
    """

    def test_unresolved_corp_code_is_logged_with_the_name_that_failed(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        claim = _claim(corp_name="존재하지않는회사")

        with caplog.at_level("WARNING", logger="disclosure_lit.pipeline"):
            result = pipeline.run_factcheck(
                claim, company_index=CompanyDisclosureIndex(), corp_code_index=CorpCodeIndex([])
            )

        assert result.items == []
        assert "존재하지않는회사" in caplog.text, "어떤 이름이 실패했는지 남아야 진단할 수 있다"

    def test_successful_resolution_does_not_warn(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """정상 경로에서는 경고가 없어야 한다 — 매 조회마다 경고가 찍히면 무의미해진다."""
        monkeypatch.setattr(
            pipeline, "verify_claim", lambda *a, **k: FactCheckResult(claim="c", items=[])
        )

        with caplog.at_level("WARNING", logger="disclosure_lit.pipeline"):
            pipeline.run_factcheck(
                _claim(corp_code="00126380"),
                company_index=CompanyDisclosureIndex(),
                corp_code_index=CorpCodeIndex([]),
            )

        assert caplog.text == ""
