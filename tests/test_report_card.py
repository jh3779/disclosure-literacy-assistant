"""report/card.py::build_report 단위 테스트 — L2a/L2b 자동 분기·결합 로직.

원래 demo/app.py(L4)에 있던 결합 로직이 report/card.py(L3)로 옮겨졌다(PR #33 리뷰
지적, @powercodingman — CLAUDE.md 아키텍처 표와 실제 구현 위치가 어긋나 있었음).
B의 실제 엔진(extract_claim/verify_claim/compute_stats/match)은 아직 스텁이라
monkeypatch로 대체해 분기 로직만 검증한다.
"""

from __future__ import annotations

import pytest

from disclosure_lit.dart.client import CorpCodeIndex
from disclosure_lit.dart.ingest import DisclosureSection
from disclosure_lit.dictionary.stats import ClusterStats
from disclosure_lit.factcheck.claim import Claim
from disclosure_lit.factcheck.verify import FactCheckItem, FactCheckResult
from disclosure_lit.report import card
from disclosure_lit.retrieval.company_index import CompanyDisclosureIndex


def _section(text: str) -> DisclosureSection:
    return DisclosureSection(
        rcept_no="R1", corp_code="00126380", form_type="유상증자", section_title="제목", text=text
    )


@pytest.fixture
def _indices() -> tuple[CompanyDisclosureIndex, CorpCodeIndex, object]:
    class _EmptyPhraseIndex:
        def match(self, phrase: str) -> None:
            raise NotImplementedError

    return CompanyDisclosureIndex(), CorpCodeIndex([]), _EmptyPhraseIndex()


class TestBuildReportBranching:
    def test_no_company_signal_goes_to_phrase_only_search(
        self, monkeypatch: pytest.MonkeyPatch, _indices
    ) -> None:
        company_index, corp_code_index, phrase_index = _indices

        monkeypatch.setattr(
            card,
            "extract_claim",
            lambda text: Claim(corp_name=None, corp_code=None, keyword=text, raw_text=text),
        )
        calls: list[str] = []

        def fake_lookup(phrase: str, *, phrase_index: object) -> ClusterStats:
            calls.append(phrase)
            return ClusterStats(
                cluster_id="C1",
                sample_count=5,
                outcome_label="유상증자",
                outcome_count=2,
                is_significant=True,
            )

        monkeypatch.setattr(card, "run_dictionary_lookup", fake_lookup)

        def fail_factcheck(*args: object, **kwargs: object) -> FactCheckResult:
            raise AssertionError("종목 미특정이면 run_factcheck 을 부르면 안 된다")

        monkeypatch.setattr(card, "run_factcheck", fail_factcheck)

        result = card.build_report(
            "선제적 재무구조 개선 문구",
            company_index=company_index,
            corp_code_index=corp_code_index,
            phrase_index=phrase_index,
        )

        assert result.fact_check is None
        assert result.phrase_stats is not None
        assert calls == ["선제적 재무구조 개선 문구"]

    def test_corp_name_signal_runs_factcheck_then_annotates_with_phrase_stats(
        self, monkeypatch: pytest.MonkeyPatch, _indices
    ) -> None:
        company_index, corp_code_index, phrase_index = _indices

        monkeypatch.setattr(
            card,
            "extract_claim",
            lambda text: Claim(
                corp_name="삼성전자", corp_code=None, keyword="유상증자", raw_text=text
            ),
        )

        fact_check = FactCheckResult(
            claim="유상증자",
            items=[
                FactCheckItem(
                    fact="유상증자 결정 사실 있음",
                    supporting_section=_section("본문1"),
                    polarity="positive",
                )
            ],
        )
        monkeypatch.setattr(card, "run_factcheck", lambda *a, **k: fact_check)

        lookup_calls: list[str] = []

        def fake_lookup(phrase: str, *, phrase_index: object) -> ClusterStats:
            lookup_calls.append(phrase)
            return ClusterStats(
                cluster_id="C1",
                sample_count=5,
                outcome_label="유상증자",
                outcome_count=2,
                is_significant=True,
            )

        monkeypatch.setattr(card, "run_dictionary_lookup", fake_lookup)

        result = card.build_report(
            "삼성전자 유상증자 루머",
            company_index=company_index,
            corp_code_index=corp_code_index,
            phrase_index=phrase_index,
        )

        assert result.fact_check is fact_check
        assert result.phrase_stats is not None
        # 화법통계는 팩트체크가 찾은 "공시 원문"에서 매칭한다 — 입력 문장이 아니라
        # supporting_section.text 를 넘겨야 한다(DESIGN.md 핵심 기능 흐름 4).
        assert lookup_calls == ["본문1"]

    def test_stops_at_first_phrase_match_across_multiple_items(
        self, monkeypatch: pytest.MonkeyPatch, _indices
    ) -> None:
        company_index, corp_code_index, phrase_index = _indices

        monkeypatch.setattr(
            card,
            "extract_claim",
            lambda text: Claim(corp_name="삼성전자", corp_code=None, keyword="k", raw_text=text),
        )

        fact_check = FactCheckResult(
            claim="k",
            items=[
                FactCheckItem(
                    fact="사실1", supporting_section=_section("본문1"), polarity="positive"
                ),
                FactCheckItem(
                    fact="사실2", supporting_section=_section("본문2"), polarity="negative"
                ),
            ],
        )
        monkeypatch.setattr(card, "run_factcheck", lambda *a, **k: fact_check)

        seen: list[str] = []

        def fake_lookup(phrase: str, *, phrase_index: object) -> ClusterStats | None:
            seen.append(phrase)
            return ClusterStats(
                cluster_id="C1",
                sample_count=5,
                outcome_label="유상증자",
                outcome_count=2,
                is_significant=True,
            )

        monkeypatch.setattr(card, "run_dictionary_lookup", fake_lookup)

        card.build_report(
            "루머",
            company_index=company_index,
            corp_code_index=corp_code_index,
            phrase_index=phrase_index,
        )

        assert seen == ["본문1"]  # 첫 매칭에서 멈춘다 — "본문2"는 조회 안 함

    def test_propagates_not_implemented_from_engines(
        self, _indices, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """엔진(L2a/L2b)이 미구현이면 NotImplementedError가 그대로 올라간다 — 호출부가
        샘플 표시 여부를 판단하는 신호.

        원래 이 테스트는 `phrase_index.match()`가 스텁이라는 사실에 기댔는데, PR #50에서
        L2b가 실구현되고 이 PR에서 `extract_claim`(L2a)도 실구현되며 더 이상 자연스럽게
        미구현 상태가 안 나온다(CI에 OPENAI_API_KEY가 없어서 `NotImplementedError` 대신
        `OpenAIError`가 나 실패했었다 — jh3779 발견, PR #51 코멘트). 이제 L2a/L2b 둘 다
        구현됐으니 "미구현 전파" 계약 자체는 임의로 하나를 미구현 취급해 직접 검증한다."""
        company_index, corp_code_index, phrase_index = _indices

        def raiser(*args: object, **kwargs: object) -> None:
            raise NotImplementedError

        monkeypatch.setattr(
            card, "extract_claim", raiser
        )  # 종목 미특정 경로로 폴백(네트워크 호출 방지)
        monkeypatch.setattr(phrase_index, "match", raiser)  # L2b를 강제로 미구현 취급

        with pytest.raises(NotImplementedError):
            card.build_report(
                "아무 문장",
                company_index=company_index,
                corp_code_index=corp_code_index,
                phrase_index=phrase_index,
            )


class TestCorpCodeOverride:
    """2026-09-18 추가 — 데모 UI 종목 선택 드롭다운(사용자 요청)이 넘기는
    `corp_code_override`가 `extract_claim`의 회사명 추출/`CorpCodeIndex.find_by_name()`의
    이름 매칭을 건너뛰고 바로 그 corp_code로 조회하는지 검증한다."""

    def test_override_is_used_even_when_claim_has_no_company_signal(
        self, monkeypatch: pytest.MonkeyPatch, _indices
    ) -> None:
        """extract_claim이 회사를 전혀 못 찾아도(corp_name=None, corp_code=None),
        override가 있으면 phrase-only 경로로 새지 않고 팩트체크를 돌린다."""
        company_index, corp_code_index, phrase_index = _indices

        monkeypatch.setattr(
            card,
            "extract_claim",
            lambda text: Claim(corp_name=None, corp_code=None, keyword=text, raw_text=text),
        )

        received_claims: list[Claim] = []

        def fake_factcheck(claim: Claim, **kwargs: object) -> FactCheckResult:
            received_claims.append(claim)
            return FactCheckResult(claim=claim.keyword, items=[])

        monkeypatch.setattr(card, "run_factcheck", fake_factcheck)

        def fail_lookup(*args: object, **kwargs: object) -> None:
            raise AssertionError("phrase-only 경로로 새면 안 된다 — override로 종목이 특정됨")

        monkeypatch.setattr(card, "run_dictionary_lookup", fail_lookup)

        result = card.build_report(
            "출자 관련 문장",
            company_index=company_index,
            corp_code_index=corp_code_index,
            phrase_index=phrase_index,
            corp_code_override="00126380",
        )

        assert result.fact_check is not None
        assert len(received_claims) == 1
        assert received_claims[0].corp_code == "00126380"
        # keyword는 여전히 extract_claim이 뽑은 값을 쓴다 — override는 corp_code에만 적용.
        assert received_claims[0].keyword == "출자 관련 문장"

    def test_override_wins_over_extracted_corp_name(
        self, monkeypatch: pytest.MonkeyPatch, _indices
    ) -> None:
        """extract_claim이 다른 회사명을 뽑았더라도(예: 오인식), override가 있으면
        그 값을 그대로 쓴다 — 드롭다운에서 사용자가 명시적으로 고른 값이 우선."""
        company_index, corp_code_index, phrase_index = _indices

        monkeypatch.setattr(
            card,
            "extract_claim",
            lambda text: Claim(corp_name="다른회사", corp_code=None, keyword="k", raw_text=text),
        )

        received_claims: list[Claim] = []

        def fake_factcheck(claim: Claim, **kwargs: object) -> FactCheckResult:
            received_claims.append(claim)
            return FactCheckResult(claim=claim.keyword, items=[])

        monkeypatch.setattr(card, "run_factcheck", fake_factcheck)
        monkeypatch.setattr(card, "run_dictionary_lookup", lambda *a, **k: None)

        card.build_report(
            "루머",
            company_index=company_index,
            corp_code_index=corp_code_index,
            phrase_index=phrase_index,
            corp_code_override="00266961",
        )

        assert received_claims[0].corp_code == "00266961"

    def test_no_override_keeps_existing_behavior(
        self, monkeypatch: pytest.MonkeyPatch, _indices
    ) -> None:
        """override를 안 넘기면(기본값 None) 기존 동작(phrase-only 폴백)이 그대로다."""
        company_index, corp_code_index, phrase_index = _indices

        monkeypatch.setattr(
            card,
            "extract_claim",
            lambda text: Claim(corp_name=None, corp_code=None, keyword=text, raw_text=text),
        )
        monkeypatch.setattr(
            card,
            "run_dictionary_lookup",
            lambda phrase, *, phrase_index: ClusterStats(
                cluster_id="C1",
                sample_count=5,
                outcome_label="유상증자",
                outcome_count=2,
                is_significant=True,
            ),
        )

        def fail_factcheck(*args: object, **kwargs: object) -> None:
            raise AssertionError("override 없이는 phrase-only 경로로 가야 한다")

        monkeypatch.setattr(card, "run_factcheck", fail_factcheck)

        result = card.build_report(
            "문장",
            company_index=company_index,
            corp_code_index=corp_code_index,
            phrase_index=phrase_index,
        )

        assert result.fact_check is None
        assert result.phrase_stats is not None


class TestResolveClaim:
    """PR #33 리뷰 지적(@powercodingman) — extract_claim 미구현이 build_report 전체를
    죽이면 안 된다(어떤 입력이든 똑같은 화면만 나오게 됨)."""

    def test_falls_back_to_company_less_claim_when_extract_claim_is_stub(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """extract_claim(B 담당)이 이제 실구현이라 여기서는 그 스텁 상태를 직접
        흉내낸다(monkeypatch) — 실제 LLM 호출 없이 `resolve_claim`의 폴백 동작만
        검증한다(2026-09-11, B 엔진 착수 시점에 실API 호출 회귀로 발견·수정)."""

        def raiser(text: str):
            raise NotImplementedError

        monkeypatch.setattr(card, "extract_claim", raiser)

        claim = card.resolve_claim("선제적 재무구조 개선")

        assert claim.corp_code is None
        assert claim.corp_name is None
        assert claim.keyword == "선제적 재무구조 개선"
        assert claim.raw_text == "선제적 재무구조 개선"

    def test_uses_real_extract_claim_when_implemented(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            card,
            "extract_claim",
            lambda text: Claim(
                corp_name="삼성전자", corp_code="00126380", keyword="유상증자", raw_text=text
            ),
        )

        claim = card.resolve_claim("삼성전자 유상증자 루머")
        assert claim.corp_code == "00126380"


class TestResolveClaimFallbackIsObservable:
    """2026-09-24 구조 감사 후속 — `extract_claim()`(L2a 엔진)이 없을 때의 폴백 기록.

    폴백은 종목 미특정 Claim을 만들어 화면을 살려두는 의도된 동작이지만, 기록이 없으면
    "엔진이 빠진 채로 돌고 있다"는 사실이 어디에도 남지 않는다.
    """

    def test_engine_missing_fallback_is_logged(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        def _stub(text: str) -> Claim:
            raise NotImplementedError

        monkeypatch.setattr(card, "extract_claim", _stub)

        with caplog.at_level("WARNING", logger="disclosure_lit.report.card"):
            claim = card.resolve_claim("삼성전자 유상증자 루머")

        assert claim.corp_code is None
        assert caplog.text, "엔진이 빠진 채 동작 중이라는 사실이 로그에 남아야 한다"
