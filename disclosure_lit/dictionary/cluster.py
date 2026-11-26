"""상투구 클러스터링 — L2b. 담당: C(우지훈).

2026-09-11 파트 분담 정정 — 03-integrated-proposal.md 11절(원안, 2026-09-10 13:15 채택)은
L2b를 C 담당으로 명시했으나(C가 L3에서 L2b 결과를 조립해야 해서), 11분 뒤 작성된 이 파일의
최초 스켈레톤 커밋(28675d3)이 담당자를 B로 잘못 적었고 CLAUDE.md 팀 분담 표도 그걸 그대로
베껴 같이 틀렸다. 재협의 흔적 없음 확인 후 원안 기준으로 되돌림.

**2026-09-21 알고리즘 교체(그리디 단일 패스 → 응집형 계층 클러스터링)**: 실측(114개사
코퍼스, 후보 1,875개)으로 그리디 단일 패스가 입력 순서에 따라 "어떤 문구끼리 묶이는가"를
20~25% 바꾼다는 게 확인됐다(가나다순·빈도순·무작위 2종 파티션 간 Jaccard 0.75~0.79).
2026-09-17의 입력 정렬(Issue #80)은 결과를 *재현 가능하게* 만들었을 뿐 임의의 순서
하나를 고정한 것이라, 회사가 하나만 추가돼도 문구 집합이 바뀌며 소속이 뒤섞였다.
average-linkage 응집형 클러스터링은 순서와 무관하게 같은 파티션을 내고(순서를 섞어도
Jaccard 1.00), 같은 실측에서 순도 지표도 동일했다(순도>=0.5 97.7% vs 97.8%). 거리
행렬이 O(n²)라 후보가 수만 개로 커지면 다른 방법이 필요하지만, `candidates.py`의
사전 필터를 거친 풀은 1만 개 규모라 문제없다(1,875개 기준 1.3초).

정렬은 그대로 둔다 — 알고리즘 자체는 순서 독립적이지만, 거리 동점의 병합 순서와
`cluster_id`·대표 문구의 동점 처리를 결정적으로 만들려면 입력 순서가 고정돼야 한다.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import pdist

from disclosure_lit.embed import embed_texts
from disclosure_lit.retrieval.phrase_index import PhraseCluster

# company_index.py의 DEFAULT_MIN_SCORE=0.3처럼 실측 근거가 아직 없다 — 실제 공시
# 상투구 데이터로 라벨링 작업(DESIGN.md Phase 2)이 시작되면 재조정이 필요한 임시값.
# 문구 클러스터링은 "관련 있으면 통과"가 아니라 "거의 같은 말이면 묶는다"가 목적이라
# company_index의 문턱(0.3)보다 훨씬 높게 잡았다. 2026-09-21 실측: 0.65~0.95 전 구간에서
# form_type 순도 지표가 사실상 변하지 않았다(순도>=0.5 96.8~97.8%) — 이 값은 "묶이는
# 정도"만 바꾸지 품질 지표를 움직이는 손잡이는 아니다.
CLUSTER_MIN_SCORE = 0.75

# `cache_store.phrase_fingerprint()`가 참조한다 — 클러스터링 알고리즘이 바뀌면 올려서,
# 다른 알고리즘으로 만든 디스크 캐시(클러스터 정의 + 그 cluster_id를 참조하는 라벨)가
# 조용히 재사용되지 않게 한다. 1 = 그리디 단일 패스(2026-09-13~), 2 = 응집형(2026-09-21~).
CLUSTERING_VERSION = 2


@dataclass(frozen=True)
class ClusteringResult:
    """`cluster_phrases_detailed()`의 결과 — `PhraseCluster`(ENT-005 고정 스키마)에 못
    싣는 멤버 구성과 centroid를 함께 돌려준다. `phrases`는 공백 문구를 뺀 뒤 정렬한
    입력이고, `members`/`centroids`/`clusters`는 클러스터 순서(= cluster_id 순서)로 1:1
    대응한다. `members`의 원소는 `phrases`의 인덱스다."""

    phrases: list[str]
    members: list[list[int]]
    centroids: np.ndarray
    clusters: list[PhraseCluster]


def _normalized_mean(vectors: np.ndarray) -> np.ndarray:
    mean_vec = vectors.mean(axis=0)
    norm = np.linalg.norm(mean_vec)
    return mean_vec / norm if norm > 0 else mean_vec


def _agglomerative_members(vectors: np.ndarray) -> list[list[int]]:
    """average-linkage 응집형 클러스터링 — 코사인 거리(1-cos)가 `1-CLUSTER_MIN_SCORE`
    이하인 동안 병합한다. 반환 순서는 각 클러스터의 정렬상 첫 멤버 순(결정적)."""
    if vectors.shape[0] == 1:
        return [[0]]
    # 정규화된 벡터끼리도 부동소수 오차로 거리가 -1e-7처럼 나올 수 있어 0으로 자른다.
    distances = np.clip(pdist(vectors, metric="cosine"), 0.0, None)
    labels = fcluster(
        linkage(distances, method="average"),
        t=1.0 - CLUSTER_MIN_SCORE,
        criterion="distance",
    )
    by_label: dict[int, list[int]] = {}
    for i, label in enumerate(labels):
        by_label.setdefault(int(label), []).append(i)
    return sorted(by_label.values(), key=lambda member_indices: member_indices[0])


def cluster_phrases_detailed(phrases: list[str]) -> ClusteringResult:
    """임베딩 유사도 기반으로 상투구를 클러스터링하고, 멤버 구성·centroid까지 돌려준다.

    공백 문구는 건너뛰고, 입력은 처리 전에 정렬한다(모듈 docstring 참고). 클러스터
    ID는 `pc-0000`부터 정렬상 첫 멤버 순으로 매긴다. 대표 문구는 centroid에 가장
    가까운 멤버(medoid)다 — 예전에는 정렬상 첫 멤버였는데, 따옴표·괄호·숫자로
    시작하는 문구가 한글보다 앞에 정렬되는 탓에 표 조각이 대표로 뽑히곤 했다(실측:
    크기 29 클러스터의 대표가 5회 등장 문구였고, 같은 클러스터의 1,046회 등장 문구는
    대표가 아니었음). centroid는 멤버 평균을 L2 정규화한 것으로, `PhraseClusterIndex.
    build()`가 그대로 인덱스에 넣는다(대표 문구 재임베딩 근사 대신 — 그쪽 docstring).

    입력에 같은 문구가 여러 번 있으면 각각 관측 하나로 센다(`sample_count`에 반영) —
    set()으로 접지 않는다.
    """
    sorted_phrases = sorted(p for p in phrases if p.strip())
    if not sorted_phrases:
        return ClusteringResult([], [], np.zeros((0, 0), dtype=np.float32), [])

    vectors = np.ascontiguousarray(embed_texts(sorted_phrases), dtype=np.float32)
    members = _agglomerative_members(vectors)
    centroids = np.stack([_normalized_mean(vectors[m]) for m in members]).astype(np.float32)

    clusters: list[PhraseCluster] = []
    for idx, (member_indices, centroid) in enumerate(zip(members, centroids, strict=True)):
        # 수학적으로 동거리인 멤버들(예: centroid 양쪽에 대칭으로 놓인 둘)이 float32
        # 오차로 갈리지 않도록, 최댓값과 1e-6 안에 드는 첫(정렬상 앞) 멤버를 대표로 삼는다.
        similarities = vectors[member_indices] @ centroid
        medoid = member_indices[int(np.argmax(similarities >= similarities.max() - 1e-6))]
        clusters.append(
            PhraseCluster(
                cluster_id=f"pc-{idx:04d}",
                representative_phrase=sorted_phrases[medoid],
                sample_count=len(member_indices),
            )
        )
    return ClusteringResult(sorted_phrases, members, centroids, clusters)


def cluster_phrases(phrases: list[str]) -> list[PhraseCluster]:
    """`cluster_phrases_detailed()`의 클러스터 목록만 돌려주는 얇은 래퍼(기존 호출부 호환)."""
    return cluster_phrases_detailed(phrases).clusters
