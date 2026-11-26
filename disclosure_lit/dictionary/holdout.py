"""L2b 화법통계의 split-half 재검정 — 학습 절반의 유의 결과를 검증 절반으로 확인.

원래 `eval/l2b_holdout.py`에 있었는데, 도메인(`dictionary/recurrence.py`)이 이 검정을
쓰려고 평가 계층(L5)을 import하고 있었다(L2b -> L5 역방향, 2026-09-24 구조 감사).
검정 자체는 `dictionary/stats.py`의 집계 위에서 도는 통계 로직이라 이쪽으로 옮기고,
평가 CLI는 여기를 가져다 쓴다 — `tests/test_layer_boundaries.py` 참고.

방법과 근거(왜 재클러스터링을 하지 않는지, 왜 상위 클러스터만 보는지)는
`eval/l2b_holdout.py` 모듈 docstring에 있다.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from disclosure_lit.dictionary.stats import (
    ClusterStats,
    OutcomeLabel,
    compute_all_stats,
    compute_stats_for_outcome,
    register_outcome_labels,
)


def split_labels_by_company(
    labels: list[OutcomeLabel], *, holdout_fraction: float = 0.5, seed: int = 42
) -> tuple[list[OutcomeLabel], list[OutcomeLabel]]:
    """회사(corp_code) 단위로 train/holdout을 나눈다.

    라벨 단위로 무작위 분할하면 같은 회사의 라벨이 train과 holdout에 걸쳐 쪼개질 수
    있는데, 그러면 회사 단위 중복 제거(모듈 docstring 참고)가 분할 경계에서 깨진다
    — 회사는 통째로 한쪽에만 속해야 한다.
    """
    corp_codes = sorted({label.corp_code for label in labels})
    rng = random.Random(seed)
    rng.shuffle(corp_codes)
    cutoff = int(len(corp_codes) * (1 - holdout_fraction))
    train_corp_codes = set(corp_codes[:cutoff])
    train = [label for label in labels if label.corp_code in train_corp_codes]
    holdout = [label for label in labels if label.corp_code not in train_corp_codes]
    return train, holdout


@dataclass
class HoldoutCheck:
    cluster_id: str
    outcome_label: str
    train_stats: ClusterStats
    holdout_stats: ClusterStats
    replicated: bool  # 검증 절반에서도 exceeds_baseline인가


def validate_significant_clusters(
    all_labels: list[OutcomeLabel],
    cluster_ids: list[str],
    *,
    holdout_fraction: float = 0.5,
    fdr_q: float = 0.05,
    seed: int = 42,
) -> list[HoldoutCheck]:
    """학습 절반에서 유의미했던 클러스터만, 검증 절반으로 재검정한다.

    학습 절반에서 아무것도 유의미하지 않으면(흔한 결과 — 표본이 반으로 줄어드니
    검정력도 떨어진다) 검증할 대상 자체가 없다는 뜻으로 빈 리스트를 반환한다(에러 아님).

    `dictionary.stats`의 전역 라벨 저장소(`register_outcome_labels()`)를 학습/검증
    절반으로 번갈아 바꿔가며 쓰고, 끝나면 반드시 `all_labels`로 원복한다 — 호출부가
    이 함수 실행 후에도 평소처럼 `compute_stats()` 등을 쓸 수 있어야 한다.
    """
    train, holdout = split_labels_by_company(
        all_labels, holdout_fraction=holdout_fraction, seed=seed
    )

    try:
        register_outcome_labels(train)
        train_significant = [
            s for s in compute_all_stats(cluster_ids, fdr_q=fdr_q) if s.exceeds_baseline
        ]

        register_outcome_labels(holdout)
        results = []
        for s in train_significant:
            holdout_stats = compute_stats_for_outcome(s.cluster_id, s.outcome_label)
            results.append(
                HoldoutCheck(
                    cluster_id=s.cluster_id,
                    outcome_label=s.outcome_label,
                    train_stats=s,
                    holdout_stats=holdout_stats,
                    replicated=holdout_stats.exceeds_baseline,
                )
            )
        return results
    finally:
        register_outcome_labels(all_labels)
