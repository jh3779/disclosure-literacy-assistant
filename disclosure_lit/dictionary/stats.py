"""클러스터별 사후 이벤트 통계 — L2b. 담당: C(우지훈).

2026-09-11 원안(proposal 11절) 기준으로 정정 — 자세한 경위는 cluster.py 참고.

**2026-09-15 기저율 보정 추가(PR #57 후속, A)**: 실 코퍼스(100개사) 전체를 끝까지
돌려서 나온 "유의미한" 클러스터 상위권을 실제로 읽어보니, K-IFRS 회계 주석처럼
거의 모든 공시에 무차별하게 등장하는 문구들이 대부분이었고, 이런 클러스터의
`outcome_label`이 하나같이 "공급계약_체결"로 쏠려 있었다. 원인을 실측으로 확인:
`공급계약_체결`이 코퍼스 전체 사후 이벤트 중 42.0%(1,855/4,414건)를 차지하는
압도적 1위였다 — 즉 회계 주석처럼 매칭 표본이 사실상 "코퍼스 전체의 무작위 표본"에
가까운 클러스터는 최빈값이 그냥 코퍼스 전체 1위로 수렴할 뿐, 문구 자체와는 인과
관계가 없는 착시였다. `is_significant`(표본 크기)와는 별개로 "기저율 대비 얼마나
벗어나는가"를 보는 `exceeds_baseline`을 추가했다 — 단순 lift(관측비율/기저율) 대신
표본 크기를 반영하는 단측 비율 z-검정을 썼다(표본 3건짜리가 우연히 100%인 것과
표본 900건짜리가 42%인 것은 통계적 무게가 다르다). 다중비교(클러스터 수백 개를
동시에 검정) 때문에 p<0.05(z>=1.96)가 아니라 p<0.01(z>=2.58)로 보수적으로 잡았다.

**2026-09-15 회사 단위 중복 제거 추가(기저율 보정 후속, A)**: 기저율 보정을 실제로
돌려서 나온 상위 결과("사업의 내용'을 참조하시기 바랍니다." → 902건 중 368건
영업정지)를 검증하다 발견 — 이 "902건"이 서로 다른 902개 회사의 독립 관측이 아니라
**33개 회사가 반복해서 낸 공시 255건**이었다(1위 회사 혼자 21번). 한 회사가 같은
상투구를 반복해서 쓰는 건 그 회사 하나의 성향일 뿐인데, z-검정은 이걸 독립적인
증거 여러 건으로 잘못 세고 있었다(pseudoreplication) — `sample_count`가 실제
독립 표본 수보다 부풀려져 유의성이 과대평가됐다. `OutcomeLabel`에 `corp_code`를
추가하고, `compute_stats()`/`_corpus_base_rate()` 둘 다 "회사당 이 outcome_label을
한 번이라도 관측했는가"만 세도록(한 회사가 같은 클러스터·같은 outcome을 몇 번
반복하든 1건으로만 집계) 바꿨다 — A11의 `min_companies`(회사 빈도 필터)와 같은
원칙을 여기도 적용한 것이다.
"""

from __future__ import annotations

import dataclasses
import json
import math
from dataclasses import dataclass
from pathlib import Path

# CLAUDE.md 주의점 3 "예: 3건" 그대로 채택 — 실제 임계값은 라벨링 데이터가 쌓이면
# 통계적으로 재검증 필요(임시값).
MIN_SIGNIFICANT_SAMPLES = 3

# 단측 z-검정 임계값(p<0.01) — 위 모듈 docstring "기저율 보정" 설명 참고. `compute_stats()`
# 클러스터 하나만 볼 때(실시간 조회, pipeline.py::run_dictionary_lookup)는 "지금 전체
# 클러스터가 몇 개인지" 맥락이 없어서 이 고정값을 그대로 쓴다 — 클러스터 수백 개(실측
# 795개) 기준으로 어림잡아 고른 보수적인 값이라, 코퍼스가 커지거나 작아지면 더는 맞지
# 않는다(2026-09-17 정정). 클러스터 여러 개를 한 번에 검정하는 오프라인 경로(평가·재검증)
# 에서는 이 고정값 대신 `compute_all_stats()`가 그때그때 클러스터 개수에 맞춰 다시
# 계산하는 Benjamini-Hochberg(FDR) 보정을 쓴다 — 아래 해당 함수 docstring 참고.
BASELINE_Z_THRESHOLD = 2.58


@dataclass
class ClusterStats:
    cluster_id: str
    sample_count: int
    outcome_label: str  # 예: "유상증자"
    outcome_count: int
    is_significant: bool  # 표본 부족(예: 3건 이하) 시 False
    base_rate: float = 0.0  # 코퍼스 전체에서 outcome_label 이 차지하는 비율(투명성용)
    z_score: float = 0.0  # 기저율 대비 단측 z-검정 통계량 — compute_all_stats()의 재보정용
    exceeds_baseline: bool = False  # 기저율 대비 유의하게 높은가(단일 조회는 BASELINE_Z_
    # THRESHOLD 고정값, 배치 조회는 compute_all_stats()의 FDR 보정값 — 둘 중 호출부가
    # 쓴 경로에 따라 의미가 다르다)


@dataclass(frozen=True)
class OutcomeLabel:
    """클러스터 하나에 대한 "그 이후 실제로 무슨 일이 있었나" 라벨 한 건.

    사후 이벤트 라벨링(A12, `scripts/label_post_events.py`)은 `normalize_form_type()`
    분류 + 시간창(3개월) 조인으로 전량 자동 처리한다 — 수동 검수 단계는 없고, 그게
    의도한 설계다(2026-09-17 정정, DESIGN.md도 동일). `demo/app.py::_outcome_labels()`가
    이 결과를 `register_outcome_labels()`로 등록한다.

    `corp_code`: 회사 단위 중복 제거(모듈 docstring "회사 단위 중복 제거" 참고)에
    필요하다 — 한 회사가 같은 상투구를 여러 공시에서 반복해도 `compute_stats()`가
    독립적인 증거 여러 건으로 잘못 세지 않도록, 어느 회사의 라벨인지 알아야 한다.
    """

    cluster_id: str
    corp_code: str
    outcome_label: str


def load_post_event_labels(raw_dir: Path) -> dict[str, list[str]]:
    """A12(scripts/label_post_events.py)가 만든 post_event_labels.jsonl을 읽는다.

    반환값은 rcept_no -> 사후 이벤트 목록(form_type 리스트) 매핑이다. 파일이
    없으면(A12를 아직 안 돌렸으면) 빈 dict — 호출부(demo/app.py)가 이 경우 아무 라벨도
    등록하지 않아 `compute_stats()`가 예전처럼 빈 통계(`is_significant=False`)를
    반환한다. `is_censored=True`인 라벨은 "그 기간을 아직 다 못 봤다"는 뜻이라(우측
    절단, label_post_events.py 참고) 빼고, `outcome_events`가 있는 것만 남긴다.
    """
    path = raw_dir / "post_event_labels.jsonl"
    if not path.exists():
        return {}
    labels: dict[str, list[str]] = {}
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if row.get("is_censored") or not row.get("outcome_events"):
                continue
            labels[row["rcept_no"]] = row["outcome_events"]
    return labels


_labels: list[OutcomeLabel] = []


def register_outcome_labels(labels: list[OutcomeLabel]) -> None:
    """라벨 저장소를 통째로 교체한다(누적이 아니라 스냅샷 — 재적재 시 중복 방지)."""
    global _labels
    _labels = list(labels)


def _company_outcomes(labels: list[OutcomeLabel]) -> dict[str, set[str]]:
    """corp_code -> 그 회사에 대해 관측된 outcome_label 집합.

    한 회사가 같은 클러스터·같은 outcome을 여러 공시에서 반복해도 집합이라 1개로만
    잡힌다(회사 단위 중복 제거, 모듈 docstring 참고) — 그 회사가 서로 다른 outcome을
    여러 개 보였다면(예: 영업정지도 있었고 제재_벌금도 있었다면) 그건 각각 셀 대상이다.
    """
    result: dict[str, set[str]] = {}
    for label in labels:
        result.setdefault(label.corp_code, set()).add(label.outcome_label)
    return result


def _corpus_base_rate(outcome_label: str) -> float:
    """등록된 전체 회사(클러스터 무관, 회사 단위 중복 제거) 중 이 outcome_label 을
    한 번이라도 관측한 회사의 비율.

    "코퍼스 전체에서 원래 이 정도 빈도로 흔한 이벤트인가"를 재는 기준값 —
    `compute_stats()`의 기저율 보정(모듈 docstring 참고)이 이 값과 클러스터별 관측
    비율을 비교한다. 라벨 건수가 아니라 회사 수로 재는 이유는 모듈 docstring "회사
    단위 중복 제거" 참고 — 한 회사의 반복 제출이 기저율 자체를 왜곡하는 것도 막는다.
    """
    all_outcomes = _company_outcomes(_labels)
    if not all_outcomes:
        return 0.0
    matches = sum(1 for outcomes in all_outcomes.values() if outcome_label in outcomes)
    return matches / len(all_outcomes)


def _stats_from_labels(cluster_id: str, cluster_labels: list[OutcomeLabel]) -> ClusterStats:
    """`compute_stats()`/`compute_pooled_stats()`가 공유하는 집계 본체.

    가장 흔한 outcome_label 하나를 대표값으로 뽑고, 몇 개 회사가 그 값을 보였는지
    센다("과거 N개사 중 M개사가 X로 이어짐"의 N=sample_count, M=outcome_count).
    N/M 은 라벨 건수가 아니라 **회사 수**다(모듈 docstring "회사 단위 중복 제거" 참고) —
    한 회사가 같은 클러스터에 여러 번 매칭돼도(상투구를 반복 사용) 1건으로만 잡힌다.
    라벨이 하나도 없으면(아직 라벨링 안 된 클러스터) 빈 통계 + is_significant=False.

    `exceeds_baseline`(단일 클러스터 기준): 관측 비율(outcome_count/sample_count)이
    코퍼스 전체 기저율보다 통계적으로 유의하게 높은지를 단측 비율 z-검정으로 판정한다
    (모듈 docstring 참고). `is_significant`(표본 크기 하한)와는 독립적인 별개 신호다 —
    표본은 충분해도 관측 비율이 기저율과 별 차이가 없으면(회계 주석처럼 무차별하게
    등장하는 문구) `exceeds_baseline=False`가 되어 "이 문구가 이 결과를 예측한다"는
    착시를 막는다. `compute_all_stats()`를 거치면 이 값은 FDR 보정값으로 덮어써진다.
    """
    company_outcomes = _company_outcomes(cluster_labels)
    sample_count = len(company_outcomes)
    if sample_count == 0:
        return ClusterStats(
            cluster_id=cluster_id,
            sample_count=0,
            outcome_label="",
            outcome_count=0,
            is_significant=False,
        )

    counts: dict[str, int] = {}
    for outcomes in company_outcomes.values():
        for outcome in outcomes:
            counts[outcome] = counts.get(outcome, 0) + 1
    # 2026-09-22 — 동점 시 outcome_label을 알파벳순으로 결정적으로 고른다.
    #
    # 예전엔 `max(counts.items(), key=lambda kv: kv[1])`이었는데, `max`는 동점이면
    # **먼저 순회된 것**을 돌려준다. 그 순회 순서는 `counts`의 삽입 순서이고, 삽입
    # 순서는 위 루프가 도는 `company_outcomes.values()`(각각 `set[str]`)의 순회
    # 순서를 따른다 — 파이썬 `set[str]` 순회 순서는 해시 랜덤화(PYTHONHASHSEED)로
    # **프로세스마다 달라진다**. 그래서 같은 코퍼스·같은 라벨인데도 실행할 때마다
    # 대표 outcome_label이 바뀌고, 그 값으로 계산하는 base_rate·z_score·
    # exceeds_baseline까지 전부 흔들렸다.
    #
    # 실측(114개사 코퍼스, 캐시된 라벨 34,627건): 라벨이 있는 클러스터 935개 중
    # **290개(31.0%)가 최빈 outcome 동점**이었다. 그 여파로 L2b held-out 검증
    # (`eval/l2b_holdout.py`)을 같은 시드·같은 데이터로 5번 돌리면 "학습 절반에서
    # 유의미한 조합" 개수가 5·6·8·20·24개로 튀어 재현성 수치를 아예 보고할 수
    # 없었다(PYTHONHASHSEED를 고정하면 5개로 안정 — 해시 랜덤화가 원인임을 확인).
    # 실시간 경로(`pipeline.py::run_dictionary_lookup` → 데모 화면)도 같은 문제라,
    # 앱을 재시작할 때마다 같은 클러스터가 다른 사후 이벤트를 보여줄 수 있었다.
    #
    # 결정적 클러스터링(PR #89)으로 cluster_id는 고정했는데 그 위에 얹힌 통계
    # 계층이 비결정적이면 의미가 없다 — 같은 원칙을 여기까지 확장한다.
    outcome_label, outcome_count = min(counts.items(), key=lambda kv: (-kv[1], kv[0]))

    base_rate = _corpus_base_rate(outcome_label)
    observed_rate = outcome_count / sample_count
    if 0 < base_rate < 1:
        standard_error = math.sqrt(base_rate * (1 - base_rate) / sample_count)
        z_score = (observed_rate - base_rate) / standard_error if standard_error > 0 else 0.0
    else:
        z_score = 0.0

    return ClusterStats(
        cluster_id=cluster_id,
        sample_count=sample_count,
        outcome_label=outcome_label,
        outcome_count=outcome_count,
        is_significant=sample_count >= MIN_SIGNIFICANT_SAMPLES,
        base_rate=base_rate,
        z_score=z_score,
        exceeds_baseline=z_score >= BASELINE_Z_THRESHOLD,
    )


def compute_stats(cluster_id: str) -> ClusterStats:
    """클러스터 하나에 라벨링된 사후 이벤트를 집계한다(단일 조회, 실시간 경로).

    `exceeds_baseline`은 고정 임계값(`BASELINE_Z_THRESHOLD`)으로 판정한다 — 실시간
    조회(`pipeline.py::run_dictionary_lookup`)는 매 요청마다 전체 클러스터를 다시
    검정할 수 없어서(성능), "지금 클러스터가 총 몇 개인지" 맥락 없이 판정해야 한다.
    여러 클러스터를 한 번에 제대로 다중비교 보정하려면 `compute_all_stats()`를 쓴다.
    """
    cluster_labels = [label for label in _labels if label.cluster_id == cluster_id]
    return _stats_from_labels(cluster_id, cluster_labels)


def compute_stats_for_outcome(cluster_id: str, outcome_label: str) -> ClusterStats:
    """`compute_stats()`처럼 최빈값을 자동으로 고르지 않고, 지정한 `outcome_label`
    하나에 대해서만 검정한다 — held-out 검증(`eval/l2b_holdout.py`, Issue #66)이 쓴다.

    학습 절반에서 유의미했던 (cluster_id, outcome_label) 조합이 검증 절반에서도
    재현되는지 봐야 하는데, 검증 절반에서는 표본이 갈려 그 outcome이 더는 최빈값이
    아닐 수 있다 — 그래도 "같은 조합"을 추적해야 재현성을 올바르게 잰다(최빈값이
    바뀌었다고 다른 조합을 검정하면 애초에 검증하려던 가설이 아니게 된다).
    """
    cluster_labels = [label for label in _labels if label.cluster_id == cluster_id]
    company_outcomes = _company_outcomes(cluster_labels)
    sample_count = len(company_outcomes)
    if sample_count == 0:
        return ClusterStats(
            cluster_id=cluster_id,
            sample_count=0,
            outcome_label=outcome_label,
            outcome_count=0,
            is_significant=False,
        )

    outcome_count = sum(1 for outcomes in company_outcomes.values() if outcome_label in outcomes)
    base_rate = _corpus_base_rate(outcome_label)
    observed_rate = outcome_count / sample_count
    if 0 < base_rate < 1:
        standard_error = math.sqrt(base_rate * (1 - base_rate) / sample_count)
        z_score = (observed_rate - base_rate) / standard_error if standard_error > 0 else 0.0
    else:
        z_score = 0.0

    return ClusterStats(
        cluster_id=cluster_id,
        sample_count=sample_count,
        outcome_label=outcome_label,
        outcome_count=outcome_count,
        is_significant=sample_count >= MIN_SIGNIFICANT_SAMPLES,
        base_rate=base_rate,
        z_score=z_score,
        exceeds_baseline=z_score >= BASELINE_Z_THRESHOLD,
    )


def compute_pooled_stats(cluster_id: str, neighbor_cluster_ids: list[str]) -> ClusterStats:
    """`cluster_id` 표본에 의미적으로 가까운 `neighbor_cluster_ids`의 표본을 더해
    검정한다 — 클러스터 하나당 표본이 구조적으로 적을 때(회사 100개, 클러스터 795개라
    클러스터당 평균 표본이 적음) 표본을 넓혀 검정력을 올리는 완화책(2026-09-17).

    "온톨로지"라고 부르기엔 과하다 — 실제로는 `PhraseClusterIndex`의 centroid 코사인
    유사도로 찾은 이웃일 뿐이고, 그 centroid 자체가 대표 문구 재임베딩 근사값이라
    근사의 근사다. `neighbor_cluster_ids`를 어떻게 고를지(유사도 임계값/개수)는 이
    함수의 책임이 아니다 — 호출부가 `PhraseClusterIndex`로 미리 찾아서 넘긴다(통계
    집계 로직과 임베딩 유사도 로직을 분리해두는 게 테스트하기 쉽다).

    같은 회사가 `cluster_id`와 이웃 클러스터 양쪽에서 같은 outcome을 보여도
    `_company_outcomes()`가 집합으로 묶어서 중복 집계되지 않는다.

    이웃을 너무 넓게 묶으면 결국 코퍼스 전체 평균(기저율 착시, 모듈 docstring 참고)에
    수렴해 다시 아무 의미도 없어진다 — 이웃 반경 자체의 타당성은 held-out 데이터로
    검증해야 한다(Issue #66, 아직 미착수). 검증 전까지는 탐색적 신호로만 쓴다.
    """
    all_ids = {cluster_id, *neighbor_cluster_ids}
    cluster_labels = [label for label in _labels if label.cluster_id in all_ids]
    return _stats_from_labels(cluster_id, cluster_labels)


def _one_sided_p_value(z_score: float) -> float:
    """z-검정 통계량 -> 단측 p-value. 표준정규분포 생존함수를 `math.erfc`로 직접
    계산해서 scipy 의존성을 새로 추가하지 않는다(표준 라이브러리 `math`만 사용)."""
    return 0.5 * math.erfc(z_score / math.sqrt(2))


def compute_all_stats(cluster_ids: list[str], *, fdr_q: float = 0.05) -> list[ClusterStats]:
    """클러스터 여러 개를 한 번에 검정하고, Benjamini-Hochberg(FDR) 절차로 `exceeds_
    baseline`을 다시 판정한다 — 오프라인 평가·재검증 전용(실시간 조회는 `compute_stats()`
    가 고정 임계값을 그대로 쓴다, 위 함수 docstring 참고).

    `compute_stats()`를 클러스터마다 따로 부르면 각자 `BASELINE_Z_THRESHOLD`라는
    고정값 하나로만 판정한다 — 이 값은 특정 클러스터 개수(N=795, 2026-09-15 실측)에
    맞춰 어림잡아 고른 값이라, 코퍼스가 커지거나 작아져 N이 달라지면 더는 맞지 않는다
    (2026-09-17 정정 — 코퍼스 확장 작업을 앞두고 N에 의존하지 않게 고쳤다). 여기서는
    "지금 실제로 몇 개를 검정하는가"(`len(cluster_ids)`)를 반영해 그때그때 다시
    계산한다 — Bonferroni(0.05/N)보다 덜 보수적이면서도(발견을 더 허용하면서도)
    거짓발견율(FDR)을 통제하는 표준 절차를 쓴다.

    표본이 하나도 없거나(sample_count=0) 기저율이 0/1이라 z-검정 자체가 정의되지
    않는 클러스터는 애초에 가설을 검정한 게 아니므로 다중비교 보정 대상(m)에서도
    제외한다 — 검정 안 한 걸 카운트에 넣으면 보정이 실제보다 느슨해진다.
    """
    all_stats = [compute_stats(cid) for cid in cluster_ids]
    testable = [s for s in all_stats if s.sample_count > 0 and 0 < s.base_rate < 1]
    if not testable:
        return all_stats

    ranked = sorted(testable, key=lambda s: _one_sided_p_value(s.z_score))
    m = len(ranked)
    threshold_p = 0.0
    for rank, stat in enumerate(ranked, start=1):
        p = _one_sided_p_value(stat.z_score)
        if p <= (rank / m) * fdr_q:
            threshold_p = p  # BH 절차: 조건을 만족하는 가장 큰 rank의 p까지 전부 채택

    significant_ids = (
        {s.cluster_id for s in ranked if _one_sided_p_value(s.z_score) <= threshold_p}
        if threshold_p > 0
        else set()
    )
    return [
        dataclasses.replace(s, exceeds_baseline=s.cluster_id in significant_ids) for s in all_stats
    ]
