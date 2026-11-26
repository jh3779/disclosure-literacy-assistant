"""`dictionary/outcome_labels.py` — 사후 이벤트 라벨 생성(A13 매칭 로직).

2026-09-24 이사: 구현이 `demo/app.py`에 있던 시절엔 이 테스트도 `test_demo_app.py`에
있었다. 평가(L5)가 화면(L4)의 비공개 함수를 쓰던 역방향 의존을 풀면서 구현을 L2b로
옮겼고(`tests/test_layer_boundaries.py` 참고), 테스트도 모듈을 따라 옮겼다.
"""

from __future__ import annotations

from disclosure_lit.dictionary.outcome_labels import build_outcome_labels
from disclosure_lit.dictionary.stats import OutcomeLabel
from tests.doubles import _FakeIndex, _outcome_section


class TestBuildOutcomeLabels:
    """A13 — A12(사후 이벤트 라벨)를 클러스터에 배선하는 순수 매칭 로직."""

    def test_matches_phrase_to_outcome_events(self) -> None:
        section = _outcome_section("R1", "선제적 재무구조 개선을 위해 계약을 체결하였습니다.")
        index = _FakeIndex({"선제적 재무구조 개선을 위해 계약을 체결하였습니다.": "pc-0001"})

        labels = build_outcome_labels(
            [section],
            {"R1": ["유상증자"]},
            index,  # type: ignore[arg-type]
        )

        assert labels == [
            OutcomeLabel(cluster_id="pc-0001", corp_code="C1", outcome_label="유상증자")
        ]

    def test_section_without_post_event_produces_no_labels(self) -> None:
        section = _outcome_section("R1", "선제적 재무구조 개선을 위해 계약을 체결하였습니다.")
        index = _FakeIndex({"선제적 재무구조 개선을 위해 계약을 체결하였습니다.": "pc-0001"})

        labels = build_outcome_labels([section], {}, index)  # type: ignore[arg-type]

        assert labels == []

    def test_unmatched_phrase_produces_no_label(self) -> None:
        section = _outcome_section("R1", "선제적 재무구조 개선을 위해 계약을 체결하였습니다.")
        index = _FakeIndex({})  # 아무 것도 매칭 안 됨

        labels = build_outcome_labels(
            [section],
            {"R1": ["유상증자"]},
            index,  # type: ignore[arg-type]
        )

        assert labels == []

    def test_multiple_outcome_events_each_produce_a_label_for_same_cluster(self) -> None:
        section = _outcome_section("R1", "선제적 재무구조 개선을 위해 계약을 체결하였습니다.")
        index = _FakeIndex({"선제적 재무구조 개선을 위해 계약을 체결하였습니다.": "pc-0001"})

        labels = build_outcome_labels(
            [section],
            {"R1": ["유상증자", "회사합병"]},
            index,  # type: ignore[arg-type]
        )

        assert sorted(labels, key=lambda label: label.outcome_label) == [
            OutcomeLabel(cluster_id="pc-0001", corp_code="C1", outcome_label="유상증자"),
            OutcomeLabel(cluster_id="pc-0001", corp_code="C1", outcome_label="회사합병"),
        ]

    def test_repeated_phrase_across_sections_matched_in_one_batch_call(self) -> None:
        """같은 문구 텍스트가 여러 섹션에 반복돼도 match_many()는 단 한 번, 중복 없는
        문구 목록으로만 호출된다(배치 매칭 — PR #57 성능 개선, _build_outcome_labels
        docstring 참고)."""
        phrase = "선제적 재무구조 개선을 위해 계약을 체결하였습니다."
        sections = [
            _outcome_section("R1", phrase),
            _outcome_section("R2", phrase),
        ]
        index = _FakeIndex({phrase: "pc-0001"})

        labels = build_outcome_labels(
            sections,
            {"R1": ["유상증자"], "R2": ["회사합병"]},
            index,  # type: ignore[arg-type]
        )

        assert index.match_many_calls == [[phrase]]  # 배치 호출 1번, 중복 없이 문구 1개
        assert len(labels) == 2

    def test_different_phrases_in_same_section_matching_same_cluster_not_double_counted(
        self,
    ) -> None:
        """같은 공시(섹션) 안의 서로 다른 두 문장이 같은 클러스터로 매칭돼도, 그 공시는
        그 클러스터에 최대 1건만 기여해야 한다 — 아니면 compute_stats()의 sample_count가
        실제 공시 건수보다 부풀려진다(PR #57 리뷰에서 재현 확인된 실제 버그)."""
        section = _outcome_section(
            "R1",
            "선제적 재무구조 개선을 위해 계약을 체결하였습니다. "
            "지속적으로 재무구조 개선을 추진하고 있습니다.",
        )
        index = _FakeIndex(
            {
                "선제적 재무구조 개선을 위해 계약을 체결하였습니다.": "pc-0001",
                # 다른 문구지만 같은 클러스터로 매칭됨
                "지속적으로 재무구조 개선을 추진하고 있습니다.": "pc-0001",
            }
        )

        labels = build_outcome_labels([section], {"R1": ["유상증자"]}, index)  # type: ignore[arg-type]

        assert labels == [
            OutcomeLabel(cluster_id="pc-0001", corp_code="C1", outcome_label="유상증자")
        ]

    def test_same_cluster_from_different_companies_each_counted(self) -> None:
        """dedup 단위는 "같은 회사"다(회사 단위 중복 제거, dictionary/stats.py 모듈
        docstring 참고) — 서로 다른 회사가 같은 클러스터·같은 outcome에 매칭되는
        정상적인 경우까지 막으면 통계 자체가 무의미해진다."""
        phrase = "선제적 재무구조 개선을 위해 계약을 체결하였습니다."
        sections = [
            _outcome_section("R1", phrase, corp_code="C1"),
            _outcome_section("R2", phrase, corp_code="C2"),
        ]
        index = _FakeIndex({phrase: "pc-0001"})

        labels = build_outcome_labels(
            sections,
            {"R1": ["유상증자"], "R2": ["유상증자"]},
            index,  # type: ignore[arg-type]
        )

        assert sorted(labels, key=lambda label: label.corp_code) == [
            OutcomeLabel(cluster_id="pc-0001", corp_code="C1", outcome_label="유상증자"),
            OutcomeLabel(cluster_id="pc-0001", corp_code="C2", outcome_label="유상증자"),
        ]

    def test_same_company_repeating_across_disclosures_counted_once(self) -> None:
        """실측 버그 재현(pseudoreplication, dictionary/stats.py 모듈 docstring
        "회사 단위 중복 제거" 참고) — 한 회사가 같은 상투구를 여러 공시에서
        반복 제출해도(예: 사업보고서마다 재활용) 그 회사는 같은 클러스터·같은
        outcome에 대해 1건으로만 집계돼야 한다. 실측에서는 한 회사가 21번
        반복한 사례가 있었다."""
        phrase = "선제적 재무구조 개선을 위해 계약을 체결하였습니다."
        sections = [
            _outcome_section("R1", phrase, corp_code="C1"),
            _outcome_section("R2", phrase, corp_code="C1"),
        ]
        index = _FakeIndex({phrase: "pc-0001"})

        labels = build_outcome_labels(
            sections,
            {"R1": ["유상증자"], "R2": ["유상증자"]},
            index,  # type: ignore[arg-type]
        )

        assert labels == [
            OutcomeLabel(cluster_id="pc-0001", corp_code="C1", outcome_label="유상증자")
        ]
