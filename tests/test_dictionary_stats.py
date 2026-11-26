"""L2b — compute_stats 단위 테스트. 사후 이벤트 통계 집계. 임베딩 불필요."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from disclosure_lit.dictionary.stats import (
    BASELINE_Z_THRESHOLD,
    MIN_SIGNIFICANT_SAMPLES,
    OutcomeLabel,
    compute_all_stats,
    compute_pooled_stats,
    compute_stats,
    compute_stats_for_outcome,
    load_post_event_labels,
    register_outcome_labels,
)


@pytest.fixture(autouse=True)
def _reset_labels() -> None:
    register_outcome_labels([])


class TestComputeStats:
    def test_no_labels_returns_empty_insignificant_stats(self) -> None:
        stats = compute_stats("pc-없음")

        assert stats.sample_count == 0
        assert stats.outcome_label == ""
        assert stats.outcome_count == 0
        assert stats.is_significant is False

    def test_picks_most_common_outcome(self) -> None:
        register_outcome_labels(
            [
                OutcomeLabel("pc-0000", "C1", "유상증자"),
                OutcomeLabel("pc-0000", "C2", "유상증자"),
                OutcomeLabel("pc-0000", "C3", "없음"),
            ]
        )

        stats = compute_stats("pc-0000")

        assert stats.sample_count == 3
        assert stats.outcome_label == "유상증자"
        assert stats.outcome_count == 2

    def test_ignores_labels_from_other_clusters(self) -> None:
        register_outcome_labels(
            [
                OutcomeLabel("pc-0000", "C1", "유상증자"),
                OutcomeLabel("pc-9999", "C1", "인력감축"),
            ]
        )

        stats = compute_stats("pc-0000")

        assert stats.sample_count == 1
        assert stats.outcome_label == "유상증자"

    def test_same_company_repeating_in_cluster_counts_once(self) -> None:
        """실측 버그 재현(PR #62 후속) — 한 회사가 같은 클러스터에 반복 매칭돼도
        (상투구를 여러 공시에서 반복 제출) 독립적인 증거 여러 건으로 잘못 세면 안
        된다(pseudoreplication). 실측에서는 한 회사가 21번 반복한 사례가 있었다 —
        여기선 3번으로 축소해 재현한다."""
        register_outcome_labels(
            [
                OutcomeLabel("pc-0000", "C1", "영업정지"),
                OutcomeLabel("pc-0000", "C1", "영업정지"),
                OutcomeLabel("pc-0000", "C1", "영업정지"),
                OutcomeLabel("pc-0000", "C2", "영업정지"),
            ]
        )

        stats = compute_stats("pc-0000")

        assert stats.sample_count == 2  # 서로 다른 회사는 C1, C2 둘뿐
        assert stats.outcome_count == 2

    def test_company_with_multiple_distinct_outcomes_counted_for_each(self) -> None:
        """회사 단위 중복 제거는 "같은 회사·같은 outcome 반복"만 막는다 — 한 회사가
        서로 다른 outcome을 여러 개 보였다면(영업정지도, 제재_벌금도) 그건 각각
        집계 대상이다."""
        register_outcome_labels(
            [
                OutcomeLabel("pc-0000", "C1", "영업정지"),
                OutcomeLabel("pc-0000", "C1", "제재_벌금"),
            ]
        )

        stats = compute_stats("pc-0000")

        assert stats.sample_count == 1  # 회사는 C1 하나뿐
        assert stats.outcome_count == 1  # 두 outcome이 동점(각 1건)이라 최빈값도 1건
        assert stats.outcome_label in ("영업정지", "제재_벌금")

    @pytest.mark.parametrize("count", [MIN_SIGNIFICANT_SAMPLES - 1, MIN_SIGNIFICANT_SAMPLES])
    def test_significance_threshold(self, count: int) -> None:
        register_outcome_labels(
            [OutcomeLabel("pc-0000", f"C{i}", "유상증자") for i in range(count)]
        )

        stats = compute_stats("pc-0000")

        assert stats.is_significant == (count >= MIN_SIGNIFICANT_SAMPLES)

    def test_register_replaces_not_accumulates(self) -> None:
        register_outcome_labels([OutcomeLabel("pc-0000", "C1", "유상증자")])
        register_outcome_labels([OutcomeLabel("pc-0000", "C1", "무상증자")])

        stats = compute_stats("pc-0000")

        assert stats.sample_count == 1
        assert stats.outcome_label == "무상증자"

    def test_tied_most_common_outcome_is_broken_deterministically(self) -> None:
        """2026-09-22 — 최빈 outcome이 동점일 때 예전 `max(...)`는 dict 삽입 순서
        (= `set[str]` 순회 순서 = 해시 랜덤화)를 따라 아무거나 골라서, 같은 데이터인데
        프로세스마다 대표 outcome이 바뀌었다. 실측(114개사 코퍼스): 라벨이 있는
        클러스터 935개 중 290개(31%)가 동점이었고, 그 여파로 L2b held-out 검증이
        같은 시드·같은 데이터로도 5~24개로 튀어 수치를 보고할 수 없었다.
        동점이면 가나다순으로 결정적으로 고른다."""
        register_outcome_labels(
            [
                OutcomeLabel("pc-0000", "C1", "유상증자"),
                OutcomeLabel("pc-0000", "C2", "무상증자"),  # 1:1 동점
            ]
        )

        stats = compute_stats("pc-0000")

        assert stats.outcome_count == 1
        assert stats.outcome_label == "무상증자"  # 가나다순으로 앞선 쪽

    def test_tie_break_does_not_override_a_clear_winner(self) -> None:
        """동점 처리는 동점일 때만 적용된다 — 표가 더 많은 쪽이 있으면 가나다
        순서와 무관하게 그쪽이 대표가 돼야 한다(가나다순으로는 "무상증자"가
        앞서지만 표는 "유상증자"가 많은 상황)."""
        register_outcome_labels(
            [
                OutcomeLabel("pc-0000", "C1", "유상증자"),
                OutcomeLabel("pc-0000", "C2", "유상증자"),
                OutcomeLabel("pc-0000", "C3", "무상증자"),
            ]
        )

        stats = compute_stats("pc-0000")

        assert stats.outcome_label == "유상증자"
        assert stats.outcome_count == 2


class TestBaselineCorrection:
    """기저율 보정(PR #57 후속) — 실측(100개사 실 코퍼스)에서 회계 주석처럼 거의 모든
    공시에 무차별하게 등장하는 클러스터가 "공급계약_체결"(코퍼스 전체 42%)로 쏠려
    "유의미"하게 보이는 착시를 재현·수정한 로직."""

    def test_cluster_matching_corpus_base_rate_does_not_exceed_baseline(self) -> None:
        """실측 버그 재현 — 클러스터의 매칭 표본이 "코퍼스 전체의 무작위 표본"에
        가까우면(=매칭된 관측 비율이 기저율과 비슷하면), 표본이 충분해도(>=3)
        기저율을 유의하게 넘지 않는다. 회사 단위 집계이므로 회사마다 서로 다른
        corp_code를 써서 표본 수가 의도한 크기로 유지되도록 한다."""
        # 코퍼스 전체: target 42%, 나머지 58% — 실측 공급계약_체결 비율(42.0%)과 유사.
        labels = [OutcomeLabel("other-cluster", f"O{i}", "target") for i in range(42)]
        labels += [OutcomeLabel("other-cluster", f"O{i}", "other") for i in range(42, 100)]
        # 회계 주석 클러스터: 관측 비율도 기저율과 비슷(45%) — 진짜 신호가 아니다.
        labels += [OutcomeLabel("accounting-boilerplate", f"A{i}", "target") for i in range(9)]
        labels += [OutcomeLabel("accounting-boilerplate", f"A{i}", "other") for i in range(9, 20)]
        register_outcome_labels(labels)

        stats = compute_stats("accounting-boilerplate")

        assert stats.sample_count == 20
        assert stats.is_significant is True  # 표본 크기 자체는 충분하다
        assert stats.outcome_label == "other"  # 11 > 9, 최빈값은 other
        assert stats.exceeds_baseline is False  # 그래도 기저율 대비 유의한 차이는 아니다

    def test_cluster_with_genuinely_distinctive_outcome_exceeds_baseline(self) -> None:
        """코퍼스 전체 기저율(10%)보다 훨씬 높은 비율(52%)로, 표본도 충분히 크면
        (n=50) 기저율을 유의하게 넘는다 — 진짜 화법-이벤트 상관관계로 볼 만하다."""
        # target 은 corpus 전체에서 25/250=10%인데, 그중 26건이 전부 이 클러스터 하나에
        # 몰려 있다 — 이 클러스터에서만 유독 자주 나온다는 뜻.
        labels = [OutcomeLabel("distinctive-cluster", f"D{i}", "target") for i in range(26)]
        labels += [OutcomeLabel("distinctive-cluster", f"D{i}", "other") for i in range(26, 50)]
        labels += [OutcomeLabel("filler-cluster", f"F{i}", "filler") for i in range(200)]
        register_outcome_labels(labels)

        stats = compute_stats("distinctive-cluster")

        assert stats.sample_count == 50
        assert stats.outcome_label == "target"
        assert stats.base_rate == pytest.approx(26 / 250)
        assert stats.exceeds_baseline is True

    def test_base_rate_field_reflects_global_proportion(self) -> None:
        labels = [OutcomeLabel("c1", f"A{i}", "유상증자") for i in range(3)]
        labels += [OutcomeLabel("c2", f"B{i}", "무상증자") for i in range(7)]
        register_outcome_labels(labels)

        stats = compute_stats("c1")

        assert stats.base_rate == pytest.approx(3 / 10)

    def test_no_labels_has_zero_base_rate_and_does_not_exceed_baseline(self) -> None:
        stats = compute_stats("pc-없음")

        assert stats.base_rate == 0.0
        assert stats.exceeds_baseline is False

    def test_uniform_outcome_across_whole_corpus_does_not_crash_or_exceed(self) -> None:
        """base_rate=1.0(코퍼스 전체가 같은 outcome)인 극단적 경우 0으로 나누기 없이
        안전하게 처리돼야 한다 — 이 경우 관측 비율도 항상 1.0이라 차이가 없다."""
        labels = [OutcomeLabel("c1", f"A{i}", "유상증자") for i in range(5)]
        register_outcome_labels(labels)

        stats = compute_stats("c1")

        assert stats.base_rate == 1.0
        assert stats.exceeds_baseline is False

    def test_company_repeating_same_outcome_does_not_inflate_sample_or_significance(
        self,
    ) -> None:
        """실측 버그 재현(모듈 docstring "회사 단위 중복 제거" 참고) — 한 회사가
        902건 중 255건을 반복 제출했더라도(1위 회사 혼자 21번) 그 회사는 표본 1건일
        뿐이다. 여기선 한 회사가 30번 반복해도 sample_count는 다른 회사 수만큼만
        늘어나야 함을 확인한다."""
        labels = [OutcomeLabel("c1", "A1", "영업정지") for _ in range(30)]
        labels += [OutcomeLabel("c1", f"B{i}", "정상") for i in range(2)]
        register_outcome_labels(labels)

        stats = compute_stats("c1")

        assert stats.sample_count == 3  # A1, B0, B1 — 반복 횟수(30)와 무관
        assert stats.outcome_label == "정상"  # B0, B1 두 회사(2건) > A1 한 회사(1건)
        assert stats.outcome_count == 2

    def test_z_threshold_is_conservative_for_multiple_comparisons(self) -> None:
        """다중비교(클러스터 수백 개) 때문에 p<0.05(z>=1.96)가 아니라 p<0.01(z>=2.58)
        정도로 보수적으로 잡았다는 걸 상수 값으로 못박는다."""
        assert BASELINE_Z_THRESHOLD >= 2.58


def _binary_outcome_cluster(
    cluster_id: str, target_count: int, *, n: int = 20
) -> list[OutcomeLabel]:
    """`target_count`/`n`개 회사가 "target", 나머지가 "other"인 클러스터 라벨을 만든다."""
    labels = [OutcomeLabel(cluster_id, f"{cluster_id}-T{i}", "target") for i in range(target_count)]
    labels += [
        OutcomeLabel(cluster_id, f"{cluster_id}-O{i}", "other") for i in range(target_count, n)
    ]
    return labels


class TestComputeAllStats:
    """2026-09-17 추가 — 코퍼스 확장을 앞두고 `BASELINE_Z_THRESHOLD` 고정값이 특정
    클러스터 개수(N=795)에 맞춰 어림잡은 값이라는 문제를 고친다. 클러스터 개수가
    달라져도 그때그때 다시 계산되는 Benjamini-Hochberg(FDR) 보정으로 대체한다."""

    def test_empty_cluster_list_returns_empty(self) -> None:
        assert compute_all_stats([]) == []

    def test_untestable_clusters_pass_through_unflagged(self) -> None:
        """표본이 아예 없는 클러스터는 다중비교 보정 대상(m)에서 빠지고, 결과에는
        그대로(예전과 동일한 빈 통계로) 포함된다."""
        stats = compute_all_stats(["없음1", "없음2"])

        assert [s.sample_count for s in stats] == [0, 0]
        assert all(s.exceeds_baseline is False for s in stats)

    def test_bh_correction_flags_real_signals_and_rejects_noise(self) -> None:
        """필러 클러스터로 기저율(~22.5%)을 고정해두고, 신호가 뚜렷한 클러스터 2개
        (strong/moderate)와 기저율과 거의 같은 노이즈 클러스터 3개를 같이 검정한다.
        노이즈 3개가 늘어나도(m이 커져도) 뚜렷한 신호 2개는 계속 유의해야 하고,
        노이즈는 계속 유의하지 않아야 한다."""
        filler = _binary_outcome_cluster("filler", 196, n=980)
        strong = _binary_outcome_cluster("strong", 20, n=20)  # 100% target
        moderate = _binary_outcome_cluster("moderate", 15, n=20)  # 75% target
        noise = [
            *_binary_outcome_cluster("noise1", 4, n=20),
            *_binary_outcome_cluster("noise2", 4, n=20),
            *_binary_outcome_cluster("noise3", 4, n=20),
        ]
        register_outcome_labels([*filler, *strong, *moderate, *noise])

        results = {
            s.cluster_id: s
            for s in compute_all_stats(["strong", "moderate", "noise1", "noise2", "noise3"])
        }

        assert results["strong"].exceeds_baseline is True
        assert results["moderate"].exceeds_baseline is True
        assert results["noise1"].exceeds_baseline is False
        assert results["noise2"].exceeds_baseline is False
        assert results["noise3"].exceeds_baseline is False

    def test_fdr_q_controls_strictness(self) -> None:
        """fdr_q를 낮추면(더 엄격하게 요구하면) 같은 클러스터도 탈락할 수 있다 —
        `borderline`(z≈4.32, p≈7.8e-6, 실측 확인)은 관대한 기준(q=0.5)에선 유의하지만
        극단적으로 엄격한 기준(q=1e-6, p 문턱이 실제 p보다 작아짐)에선 탈락해야 한다."""
        filler = _binary_outcome_cluster("filler", 196, n=980)
        borderline = _binary_outcome_cluster("borderline", 12, n=20)  # 60% target
        register_outcome_labels([*filler, *borderline])

        lenient = compute_all_stats(["borderline"], fdr_q=0.5)[0]
        strict = compute_all_stats(["borderline"], fdr_q=1e-6)[0]

        assert lenient.exceeds_baseline is True
        assert strict.exceeds_baseline is False


class TestComputeStatsForOutcome:
    """2026-09-17 추가 — held-out 검증(Issue #66)이 "최빈값이 아니라 지정한 특정
    outcome"을 추적해야 해서 필요해졌다."""

    def test_matches_compute_stats_when_outcome_is_the_majority(self) -> None:
        register_outcome_labels(
            [
                OutcomeLabel("pc-0000", "C1", "유상증자"),
                OutcomeLabel("pc-0000", "C2", "유상증자"),
                OutcomeLabel("pc-0000", "C3", "없음"),
            ]
        )

        via_stats_for_outcome = compute_stats_for_outcome("pc-0000", "유상증자")
        via_compute_stats = compute_stats("pc-0000")

        assert via_stats_for_outcome == via_compute_stats

    def test_tracks_minority_outcome_unlike_compute_stats(self) -> None:
        """compute_stats()는 최빈값("없음", 2건)을 자동으로 고르지만, 여기서는
        소수 outcome("유상증자", 1건)을 명시적으로 추적할 수 있어야 한다."""
        register_outcome_labels(
            [
                OutcomeLabel("pc-0000", "C1", "유상증자"),
                OutcomeLabel("pc-0000", "C2", "없음"),
                OutcomeLabel("pc-0000", "C3", "없음"),
            ]
        )

        stats = compute_stats_for_outcome("pc-0000", "유상증자")

        assert stats.outcome_label == "유상증자"
        assert stats.outcome_count == 1
        assert stats.sample_count == 3  # 분모는 여전히 클러스터 전체 회사 수

    def test_no_labels_returns_empty_stats_with_requested_outcome_label(self) -> None:
        stats = compute_stats_for_outcome("pc-없음", "유상증자")

        assert stats.sample_count == 0
        assert stats.outcome_label == "유상증자"  # compute_stats()와 달리 빈 문자열이 아님
        assert stats.is_significant is False


class TestComputePooledStats:
    """2026-09-17 추가 — 클러스터 하나당 표본이 구조적으로 적을 때, 의미적으로 가까운
    이웃 클러스터의 표본을 더해 검정력을 올리는 완화책(온톨로지/그래프까지 갈 필요
    없다는 판단, 대화 맥락 참고)."""

    def test_pooling_combines_neighbor_labels(self) -> None:
        register_outcome_labels(
            [
                OutcomeLabel("small", "C1", "유상증자"),
                OutcomeLabel("small", "C2", "유상증자"),
                OutcomeLabel("neighbor", "C3", "유상증자"),
                OutcomeLabel("neighbor", "C4", "유상증자"),
            ]
        )

        alone = compute_stats("small")
        pooled = compute_pooled_stats("small", ["neighbor"])

        assert alone.sample_count == 2
        assert alone.is_significant is False  # MIN_SIGNIFICANT_SAMPLES=3 미달
        assert pooled.sample_count == 4
        assert pooled.is_significant is True
        assert pooled.cluster_id == "small"  # 결과의 정체성은 원래 클러스터 기준

    def test_pooling_does_not_double_count_same_company(self) -> None:
        """같은 회사가 두 클러스터 모두에서 같은 outcome을 보였다면 중복 집계되면
        안 된다(모듈 docstring "회사 단위 중복 제거"와 동일한 원칙)."""
        register_outcome_labels(
            [
                OutcomeLabel("small", "C1", "유상증자"),
                OutcomeLabel("neighbor", "C1", "유상증자"),  # 같은 회사, 다른 클러스터
                OutcomeLabel("neighbor", "C2", "유상증자"),
            ]
        )

        pooled = compute_pooled_stats("small", ["neighbor"])

        assert pooled.sample_count == 2  # C1, C2 — C1은 한 번만

    def test_pooling_with_no_neighbors_matches_compute_stats(self) -> None:
        register_outcome_labels([OutcomeLabel("small", "C1", "유상증자")])

        assert compute_pooled_stats("small", []) == compute_stats("small")


def _write_labels_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8"
    )


class TestLoadPostEventLabels:
    """A12(scripts/label_post_events.py)가 만든 post_event_labels.jsonl 로더."""

    def test_missing_file_returns_empty_dict(self, tmp_path: Path) -> None:
        assert load_post_event_labels(tmp_path) == {}

    def test_loads_rcept_no_to_outcome_events_mapping(self, tmp_path: Path) -> None:
        _write_labels_jsonl(
            tmp_path / "post_event_labels.jsonl",
            [
                {
                    "rcept_no": "R1",
                    "corp_code": "C1",
                    "rcept_dt": "20260101",
                    "form_type": "MOU",
                    "outcome_events": ["유상증자"],
                    "outcome_count": 1,
                    "is_censored": False,
                }
            ],
        )

        labels = load_post_event_labels(tmp_path)

        assert labels == {"R1": ["유상증자"]}

    def test_censored_labels_excluded(self, tmp_path: Path) -> None:
        """우측 절단(is_censored=True)은 "이벤트 없음"이 아니라 "아직 다 못 봄"이라 뺀다
        (label_post_events.py 의 우측 절단 설명 참고)."""
        _write_labels_jsonl(
            tmp_path / "post_event_labels.jsonl",
            [
                {
                    "rcept_no": "R1",
                    "outcome_events": ["유상증자"],
                    "is_censored": True,
                }
            ],
        )

        assert load_post_event_labels(tmp_path) == {}

    def test_empty_outcome_events_excluded(self, tmp_path: Path) -> None:
        _write_labels_jsonl(
            tmp_path / "post_event_labels.jsonl",
            [{"rcept_no": "R1", "outcome_events": [], "is_censored": False}],
        )

        assert load_post_event_labels(tmp_path) == {}
