"""`dictionary/holdout.py` — split-half 재검정(회사 단위 분할 + 유의 클러스터 재검증).

2026-09-24 이사: 구현이 `eval/l2b_holdout.py`에 있던 시절의 테스트를 모듈을 따라 옮겼다.
도메인(`dictionary/recurrence.py`)이 이 검정을 쓰면서 평가 계층을 import하던 역방향
의존을 푼 결과다 — `tests/test_layer_boundaries.py` 참고.
"""

from __future__ import annotations

import pytest

from disclosure_lit.dictionary.holdout import (
    split_labels_by_company,
    validate_significant_clusters,
)
from disclosure_lit.dictionary.stats import OutcomeLabel, register_outcome_labels


@pytest.fixture(autouse=True)
def _reset_labels() -> None:
    register_outcome_labels([])


class TestSplitLabelsByCompany:
    def test_same_company_never_split_across_halves(self) -> None:
        """회사 하나가 라벨을 여러 개 갖고 있으면(여러 클러스터 매칭), 그 라벨들은
        전부 같은 쪽(train 또는 holdout)에만 있어야 한다."""
        labels = [
            OutcomeLabel("pc-0000", "C1", "유상증자"),
            OutcomeLabel("pc-0001", "C1", "무상증자"),  # C1의 다른 클러스터 라벨
            *[OutcomeLabel("pc-0000", f"C{i}", "유상증자") for i in range(2, 20)],
        ]

        train, holdout = split_labels_by_company(labels)

        train_c1 = any(label.corp_code == "C1" for label in train)
        holdout_c1 = any(label.corp_code == "C1" for label in holdout)
        assert train_c1 != holdout_c1  # 둘 중 정확히 한쪽에만 있어야 함(XOR)

    def test_all_labels_accounted_for_with_no_overlap(self) -> None:
        labels = [OutcomeLabel("pc-0000", f"C{i}", "유상증자") for i in range(20)]

        train, holdout = split_labels_by_company(labels)

        assert len(train) + len(holdout) == len(labels)
        train_companies = {label.corp_code for label in train}
        holdout_companies = {label.corp_code for label in holdout}
        assert train_companies & holdout_companies == set()

    def test_same_seed_is_reproducible(self) -> None:
        labels = [OutcomeLabel("pc-0000", f"C{i}", "유상증자") for i in range(20)]

        first = split_labels_by_company(labels, seed=7)
        second = split_labels_by_company(labels, seed=7)

        assert first == second

    def test_holdout_fraction_controls_split_size(self) -> None:
        labels = [OutcomeLabel("pc-0000", f"C{i}", "유상증자") for i in range(20)]

        train, holdout = split_labels_by_company(labels, holdout_fraction=0.25)

        train_companies = {label.corp_code for label in train}
        holdout_companies = {label.corp_code for label in holdout}
        assert len(holdout_companies) == 5  # 20개사의 25%
        assert len(train_companies) == 15


def _binary_outcome_labels(cluster_id: str, target_count: int, *, n: int) -> list[OutcomeLabel]:
    labels = [OutcomeLabel(cluster_id, f"{cluster_id}-T{i}", "target") for i in range(target_count)]
    labels += [
        OutcomeLabel(cluster_id, f"{cluster_id}-O{i}", "other") for i in range(target_count, n)
    ]
    return labels


class TestValidateSignificantClusters:
    def test_no_significant_clusters_in_train_returns_empty(self) -> None:
        """학습 절반에서 아무것도 안 유의미하면 검증할 대상이 없다 — 에러가 아니라
        빈 리스트."""
        # 40개사 전부 기저율과 똑같은 비율(노이즈) — 유의미한 클러스터가 없다.
        labels = _binary_outcome_labels("noise", 8, n=40)

        results = validate_significant_clusters(labels, ["noise"])

        assert results == []

    def test_strong_signal_replicates_on_holdout(self) -> None:
        """이웃/기저율과 무관하게 거의 모든 회사가 항상 target인 극단적 신호는,
        절반으로 쪼개도 양쪽 다 유의미하게 나와야 한다."""
        filler = _binary_outcome_labels("filler", 40, n=200)  # 기저율 20% 고정
        strong = _binary_outcome_labels("strong", 40, n=40)  # 100% target, 40개사

        results = validate_significant_clusters(filler + strong, ["strong"])

        assert len(results) == 1
        assert results[0].cluster_id == "strong"
        assert results[0].replicated is True

    def test_global_label_state_restored_after_call(self) -> None:
        """dictionary.stats의 전역 라벨 저장소를 학습/검증용으로 바꿔써도, 호출이
        끝나면 원래 전체 라벨로 복구돼야 한다(호출부가 이어서 compute_stats 등을
        정상적으로 쓸 수 있어야 함)."""
        from disclosure_lit.dictionary.stats import compute_stats

        labels = _binary_outcome_labels("c1", 10, n=20)
        register_outcome_labels(labels)

        validate_significant_clusters(labels, ["c1"])

        assert compute_stats("c1").sample_count == 20  # 원래 전체 라벨 그대로 복구됨

    def test_restores_state_even_if_cluster_ids_empty(self) -> None:
        labels = _binary_outcome_labels("c1", 10, n=20)
        register_outcome_labels(labels)

        results = validate_significant_clusters(labels, [])

        assert results == []
