"""form_type(공시 서식 유형) 자기상관 통계 — L2b 재설계(2026-09-17).

상투구 문구 기반 "사후 이벤트 예측"은 held-out 검증에서 반증됐다(대화 기록 — 클러스터
세밀도를 0.35~0.75 전 구간에서 조정해봐도 재현율 0%). 유일하게 반복 검증된 신호는
"같은 form_type이 일정 기간 내 다시 나타나는" 자기상관이다(단일 분할 기준 재현율
37~65%). 다만 단일 분할은 표본이 작아(클러스터/유형당 회사 1~20개) 결과가 seed에
따라 크게 흔들린다는 것도 확인됐다 — 그래서 여러 무작위 분할(기본 30회)에 걸쳐
안정적으로 재현되는 조합만 "검증된 반복성"으로 인정한다.

교차 유형(다른 유형을 예측)은 이 반복 검증에서 재현율이 노이즈 수준(8~13%, 안정
조합 0개)까지 떨어져 신뢰할 수 없다고 이미 확인됐다 — 이 모듈은 자기상관
(outcome_label == form_type)만 다룬다.

**2026-09-18**: 자기상관 자체는 통계적으로 검증됐지만, "이 유형이 미래에 다시
나타나는 경향이 있다"는 진술은 그 자체로 **미래에 대한 진술**이다 — 한때 이 이유로
데모 UI에서 뺐었다. 하지만 사용자 확인 결과 반복 경향 자체는 보여줘도 된다는 게
확정됐고(과거형 서술로만 한정한다는 전제 하에), `demo/app.py::_build_meaning_graph_dot()`
에 🔁 노드로 다시 붙었다 — 라벨은 항상 "반복돼 왔다"류 과거 시제만 쓰고
"반복될 것" 같은 미래형은 쓰지 않는다(`tests/test_meaning_graph.py::
test_recurrence_node_uses_past_tense_only` 로 고정).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from disclosure_lit.dart.ingest import DisclosureSection
from disclosure_lit.dictionary.holdout import validate_significant_clusters
from disclosure_lit.dictionary.stats import OutcomeLabel

DEFAULT_N_SEEDS = 30
DEFAULT_FDR_Q = 0.05
# 실측(대화 기록) — train 표본이 5개사 미만이면 재현율이 전반적으로 낮고 불안정했다.
DEFAULT_MIN_TRAIN_N = 5
# 반복 분할 중 이 비율 이상 재현돼야 "안정적"으로 인정한다.
DEFAULT_STABILITY_THRESHOLD = 0.5
# 최소 이만큼은 학습단계에서 유의미로 뽑혀야 안정성 점수 자체가 의미 있다.
DEFAULT_MIN_APPEARANCES = 3


@dataclass(frozen=True)
class RecurrenceSignal:
    form_type: str
    stability: float  # 반복 분할 중 재현된 비율(0~1)
    n_train_significant: int  # 몇 번의 분할에서 학습단계 유의미했는지
    n_seeds: int


def build_form_type_labels(
    sections: list[DisclosureSection], post_events: dict[str, list[str]]
) -> list[OutcomeLabel]:
    """공시 자신의 form_type을 outcome_label로 쓰는 라벨을 만든다 — 자기상관 검정
    전용이다. `cluster_id`/`outcome_label` 둘 다 form_type 문자열이라, "이 유형이
    다시 나타나는가"만 검정할 수 있게 dedup한다(같은 회사가 같은 유형을 반복
    제출해도 1건으로만 집계 — dictionary/stats.py의 회사 단위 중복 제거와 동일 원칙).
    """
    seen_rcept: set[str] = set()
    seen_dedup: set[tuple[str, str, str]] = set()
    labels: list[OutcomeLabel] = []
    for section in sections:
        if section.rcept_no in seen_rcept:
            continue
        seen_rcept.add(section.rcept_no)
        events = post_events.get(section.rcept_no)
        if not events:
            continue
        for event in events:
            key = (section.corp_code, section.form_type, event)
            if key in seen_dedup:
                continue
            seen_dedup.add(key)
            labels.append(
                OutcomeLabel(
                    cluster_id=section.form_type,
                    corp_code=section.corp_code,
                    outcome_label=event,
                )
            )
    return labels


def compute_recurrence_signals(
    labels: list[OutcomeLabel],
    form_types: list[str],
    *,
    n_seeds: int = DEFAULT_N_SEEDS,
    fdr_q: float = DEFAULT_FDR_Q,
    min_train_n: int = DEFAULT_MIN_TRAIN_N,
    stability_threshold: float = DEFAULT_STABILITY_THRESHOLD,
    min_appearances: int = DEFAULT_MIN_APPEARANCES,
) -> dict[str, RecurrenceSignal]:
    """여러 분할에 걸쳐 안정적으로 자기 자신이 재현되는 form_type만 골라낸다.

    `validate_significant_clusters()`(Issue #66)를 `n_seeds`번 반복해서, 매번
    "학습 절반에서 유의미했는가"와 "검증 절반에서도 재현됐는가"를 누적한다. 안정성
    점수(재현 횟수/학습단계 유의미 횟수)가 `stability_threshold` 이상이고, 학습단계
    유의미가 최소 `min_appearances`번 이상 나온 유형만 반환한다 — 어쩌다 한 번
    유의미했던 것과, 꾸준히 유의미한 것을 구분한다.
    """
    train_hits: dict[str, int] = defaultdict(int)
    replicated_hits: dict[str, int] = defaultdict(int)

    for seed in range(n_seeds):
        results = validate_significant_clusters(labels, form_types, fdr_q=fdr_q, seed=seed)
        for r in results:
            if r.outcome_label != r.cluster_id:  # 자기상관만 — 교차 유형은 노이즈 수준
                continue
            if r.train_stats.sample_count < min_train_n:
                continue
            train_hits[r.cluster_id] += 1
            if r.replicated:
                replicated_hits[r.cluster_id] += 1

    signals: dict[str, RecurrenceSignal] = {}
    for form_type, n_train in train_hits.items():
        if n_train < min_appearances:
            continue
        stability = replicated_hits[form_type] / n_train
        if stability < stability_threshold:
            continue
        signals[form_type] = RecurrenceSignal(
            form_type=form_type,
            stability=stability,
            n_train_significant=n_train,
            n_seeds=n_seeds,
        )
    return signals
