"""dictionary.recurrence 단위 테스트. 임베딩 불필요 — OutcomeLabel/통계 로직만."""

from __future__ import annotations

from disclosure_lit.dart.ingest import DisclosureSection
from disclosure_lit.dictionary.recurrence import build_form_type_labels, compute_recurrence_signals


def _section(corp_code: str, rcept_no: str, form_type: str) -> DisclosureSection:
    return DisclosureSection(
        rcept_no=rcept_no, corp_code=corp_code, form_type=form_type, section_title="", text=""
    )


class TestBuildFormTypeLabels:
    def test_no_events_produces_no_label(self) -> None:
        sections = [_section("C1", "R1", "유상증자")]
        assert build_form_type_labels(sections, {}) == []

    def test_creates_label_with_form_type_as_both_cluster_and_check(self) -> None:
        sections = [_section("C1", "R1", "유상증자")]
        labels = build_form_type_labels(sections, {"R1": ["유상증자"]})

        assert len(labels) == 1
        assert labels[0].cluster_id == "유상증자"
        assert labels[0].outcome_label == "유상증자"
        assert labels[0].corp_code == "C1"

    def test_duplicate_sections_of_same_disclosure_counted_once(self) -> None:
        sections = [
            _section("C1", "R1", "유상증자"),
            _section("C1", "R1", "유상증자"),  # 같은 공시의 다른 섹션(A6 청킹)
        ]
        labels = build_form_type_labels(sections, {"R1": ["유상증자"]})

        assert len(labels) == 1

    def test_multiple_events_produce_multiple_labels(self) -> None:
        sections = [_section("C1", "R1", "유상증자")]
        labels = build_form_type_labels(sections, {"R1": ["유상증자", "감자"]})

        outcomes = {label.outcome_label for label in labels}
        assert outcomes == {"유상증자", "감자"}


def _repeating_labels(form_type: str, n: int):
    """corp_code C{i}가 form_type을 트리거로, 같은 form_type을 outcome으로 갖는 라벨."""
    from disclosure_lit.dictionary.stats import OutcomeLabel

    return [OutcomeLabel(form_type, f"{form_type}-C{i}", form_type) for i in range(n)]


class TestComputeRecurrenceSignals:
    def test_no_labels_returns_empty(self) -> None:
        assert compute_recurrence_signals([], []) == {}

    def test_strong_self_recurrence_is_stable(self) -> None:
        """100% 자기상관(회사 20개 전부 같은 유형 반복)은 어떤 분할에서도 재현돼야
        한다. filler로 기저율을 1.0 미만(20%)으로 만들어야 z-검정이 정의된다
        (base_rate가 정확히 1.0이면 0으로 나누기 가드가 걸려 z=0이 돼버림)."""
        from disclosure_lit.dictionary.stats import OutcomeLabel

        filler = [OutcomeLabel("filler", f"F{i}", "공급계약_체결") for i in range(40)]
        filler += [OutcomeLabel("filler", f"F{i}", "other") for i in range(40, 200)]
        target_cluster = _repeating_labels("공급계약_체결", 20)

        signals = compute_recurrence_signals(
            filler + target_cluster,
            ["공급계약_체결"],
            n_seeds=10,
            min_train_n=3,
            min_appearances=1,
        )

        assert "공급계약_체결" in signals
        assert signals["공급계약_체결"].stability == 1.0

    def test_cross_type_never_counted_even_if_significant(self) -> None:
        """outcome_label이 cluster_id(form_type)와 다르면(교차 유형) 자기상관이
        아니므로 절대 신호로 안 잡혀야 한다 — 기저율이 낮아(감자가 드묾) 필터가 없다면
        강하게 유의미했을 조합으로 확인한다."""
        from disclosure_lit.dictionary.stats import OutcomeLabel

        # filler: 감자가 드문 기저율(200개사 중 10개사, 5%)을 만든다.
        filler = [OutcomeLabel("filler", f"F{i}", "감자") for i in range(10)]
        filler += [OutcomeLabel("filler", f"F{i}", "other") for i in range(10, 200)]
        # "유상증자" 유형 다음에 전부 "감자"(다른 유형, 100%)로 이어지는 강한 교차
        # 신호 — 기저율(5%) 대비 압도적으로 높지만, 자기상관이 아니므로 제외돼야 한다.
        cross = [OutcomeLabel("유상증자", f"C{i}", "감자") for i in range(20)]

        signals = compute_recurrence_signals(
            filler + cross, ["유상증자"], n_seeds=10, min_train_n=3, min_appearances=1
        )

        assert signals == {}

    def test_no_signal_when_observed_rate_matches_baseline(self) -> None:
        """target 클러스터 자신의 self-recurrence 비율(20%)이 코퍼스 전체 기저율
        (filler 그룹까지 합쳐 20%)과 같으면(우연히 흔한 것뿐), 학습단계에서부터
        유의미가 거의 안 나와 안정성 기준을 못 넘어야 한다."""
        from disclosure_lit.dictionary.stats import OutcomeLabel

        # filler(다른 클러스터) 200개사: target 40개사(20%), other 160개사.
        filler = [OutcomeLabel("filler", f"F{i}", "target") for i in range(40)]
        filler += [OutcomeLabel("filler", f"F{i}", "other") for i in range(40, 200)]
        # target 클러스터 20개사: 자기 자신(target)이 4개사(20%, 기저율과 동일) —
        # 진짜 신호가 아니다.
        target_cluster = [OutcomeLabel("target", f"T{i}", "target") for i in range(4)]
        target_cluster += [OutcomeLabel("target", f"T{i}", "other") for i in range(4, 20)]

        signals = compute_recurrence_signals(
            filler + target_cluster, ["target"], n_seeds=10, min_train_n=3, min_appearances=1
        )

        assert "target" not in signals
