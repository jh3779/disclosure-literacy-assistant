"""상투구 클러스터 벡터 인덱스 — L1. 담당: C(우지훈) — 2026-09-11 DEC-007과 함께 정정.

cluster.py가 만드는 PhraseCluster를 이 인덱스가 그대로 소비하고, run_dictionary_lookup
(pipeline.py, C)이 match()를 호출한다 — L2b 엔진과 같은 담당으로 통일.

**2026-09-18 FAISS 도입**: 유사도 연산 자체(브루트포스 numpy 행렬곱)는 지금 규모
(클러스터 수백~천 단위)에서 이미 충분히 빠르다 — 벡터DB가 필요한 건 코퍼스가 몇
자릿수 더 커져서(전체 KOSPI/KOSDAQ·수년치) 근사 최근접 탐색(ANN)이 실제로 이득을
주는 시점부터다. 지금은 팀 결정으로 미리 도입한다 — `faiss.IndexFlatIP`(정확 검색,
근사 아님)를 써서 기존 numpy 구현과 수치적으로 동일한 결과를 내도록 했다(회귀 없음,
`test_matches_same_results_as_repeated_match_calls` 등 기존 테스트로 검증). 나중에
실제로 근사 인덱스(HNSW/IVF)가 필요해지면 이 지점만 교체하면 된다.

**2026-09-21**: `build()`가 대표 문구 재임베딩 근사 대신 진짜 멤버 평균 centroid를
쓰고, 정확한 멤버십(`member_cluster_ids()`)을 갖는다 — 근거와 실측치는 `build()`
docstring, `dictionary/cluster.py` 모듈 docstring 참고. 클러스터링 후 회사 수로
클러스터를 버리는 필터(`min_companies`)는 같은 날 추가됐다가 다시 뺐다 — "매칭
성공 여부"와 "여러 회사 통계로 보여줄 신뢰도"를 하나로 묶으면 표본이 적은 진짜
표현까지 아예 못 찾게 된다는 팀 리뷰 지적으로, 회사 수 판정은 `dictionary/
cluster_meaning.py::MIN_COMPANIES_FOR_MEANING`으로 옮겼다(그쪽 모듈 docstring
참고) — `build()`는 이제 회사 수를 전혀 모른다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import faiss
import numpy as np

from disclosure_lit.embed import embed_query, embed_texts


@dataclass
class PhraseCluster:
    cluster_id: str
    representative_phrase: str
    sample_count: int


class PhraseClusterIndex:
    """공시 상투구 클러스터를 벡터로 인덱싱하고, 새 문장을 매칭한다."""

    def __init__(self) -> None:
        self._clusters: list[PhraseCluster] = []
        self._faiss_index: faiss.IndexFlatIP | None = None
        # build()가 클러스터링에 쓴 문구 → cluster_id. `PhraseCluster`(ENT-005 고정
        # 스키마)는 멤버 목록을 못 싣지만, 소속을 나중에 match()로 "재발견"하면 정확하지
        # 않다(2026-09-21 실측: 대표 문구 근사 centroid 시절 후보의 4.5%가 다른 클러스터로
        # 가거나 문턱에 걸려 빠짐). 디스크 캐시에서 load()할 때 없으면 None.
        self._cluster_id_by_phrase: dict[str, str] | None = None

    def build(self, phrases: list[str]) -> list[PhraseCluster]:
        """phrases를 클러스터링하고, match()가 쓸 centroid를 FAISS 인덱스에 적재한다.

        `dictionary.cluster`를 함수 본문 안에서(모듈 최상단이 아니라) import한다 —
        `dictionary/cluster.py`가 이 모듈의 `PhraseCluster`를 최상단에서 import하므로,
        여기서도 최상단에서 되받아 import하면 순환 참조가 된다.

        centroid는 `cluster_phrases_detailed()`가 계산한 멤버 평균(L2 정규화)을 그대로
        쓴다. 2026-09-21 이전에는 `PhraseCluster`에 임베딩 필드가 없다는 이유로 대표
        문구 하나를 다시 임베딩해 근사했는데, 실측(후보 1,875개)으로 그 근사 centroid로는
        클러스터링에 쓴 문구의 4.5%가 자기 클러스터에 재매칭되지 않았고(다른 클러스터
        1.2% + 문턱 미달 3.3%), 진짜 centroid로는 0%였다 — `cluster_meaning.py`가
        `match_many()`로 소속을 복원하므로 그 오차를 그대로 물려받고 있었다.

        **2026-09-21 회사 수 필터를 여기서 뺐다(팀 리뷰 지적)**: 예전에는
        `min_companies` 미만인 클러스터를 여기서 통째로 버렸는데, 그러면 "이 표현이
        무슨 뜻인지 찾는 것"(매칭)과 "여러 회사 통계로 보여줄 자격이 있는지"(신뢰도
        표시)가 하나로 묶여서, 표본이 적은 진짜 표현까지 `match()`가 아예 못 찾게
        됐다. 이제 클러스터링에 쓴 문구는 전부 클러스터로 남긴다 — 회사 수 기준
        신뢰도 판정은 `dictionary/cluster_meaning.py::compute_cluster_meanings()`가
        (매칭된 실제 코퍼스 전체를 다시 스캔해) 별도로 매긴다.

        `faiss.IndexFlatIP`(정확 내적 검색)를 쓴다 — 벡터가 L2 정규화돼 있어 내적이
        곧 코사인 유사도다. 지금 규모(클러스터 수백~천 단위)에서는 numpy 행렬곱과
        속도 차이가 없지만, 코퍼스가 훨씬 커져 근사 인덱스(HNSW/IVF)로 바꿀 필요가
        생기면 이 지점만 교체하면 되도록 미리 FAISS로 배선해둔다(모듈 docstring
        "2026-09-18 FAISS 도입" 참고).
        """
        from disclosure_lit.dictionary.cluster import cluster_phrases_detailed

        result = cluster_phrases_detailed(phrases)

        self._clusters = result.clusters
        cluster_id_by_phrase: dict[str, str] = {}
        for cluster, member_indices in zip(result.clusters, result.members, strict=True):
            for i in member_indices:
                cluster_id_by_phrase[result.phrases[i]] = cluster.cluster_id
        self._cluster_id_by_phrase = cluster_id_by_phrase

        if result.clusters:
            centroids = np.ascontiguousarray(result.centroids, dtype=np.float32)
            self._faiss_index = faiss.IndexFlatIP(centroids.shape[1])
            self._faiss_index.add(centroids)
        else:
            self._faiss_index = None
        return result.clusters

    def member_cluster_ids(self) -> dict[str, str] | None:
        """`build()`가 클러스터링에 쓴 문구 → cluster_id(정확한 소속). 디스크 캐시에서
        멤버십 없이 `load()`된 인덱스는 None — 호출부는 그때만 `match_many()`로
        소속을 근사 복원한다."""
        return self._cluster_id_by_phrase

    def dump(self) -> tuple[list[PhraseCluster], np.ndarray]:
        """직렬화용(디스크 캐싱, `cache_store.py`) — 클러스터 목록과, 그 순서에 대응하는
        centroid 행렬. 반드시 `dictionary/stats.py`의 `OutcomeLabel` 목록과 같은 원자적
        캐시 번들로만 저장/로드해야 한다(모듈 docstring의 cluster_id 비결정성 경고 참고).

        `faiss.IndexFlatIP`는 원본 벡터를 그대로 들고 있어(양자화 없음) `reconstruct_n()`
        으로 `build()`가 넣은 순서 그대로(=`self._clusters` 순서) 다시 꺼낼 수 있다."""
        centroids = (
            self._faiss_index.reconstruct_n(0, self._faiss_index.ntotal)
            if self._clusters and self._faiss_index is not None
            else np.zeros((0, 0), dtype=np.float32)
        )
        return self._clusters, centroids

    def load(
        self,
        clusters: list[PhraseCluster],
        centroids: np.ndarray,
        cluster_id_by_phrase: Mapping[str, str] | None = None,
    ) -> None:
        """`dump()`가 만든 (클러스터, centroid)로 인덱스를 재구성한다(재클러스터링 없음).

        디스크 캐시 로드 전용 — 저장된 centroid를 그대로 FAISS 인덱스에 넣는다.
        `centroids`는 반드시 `dump()`가 만든 순서 그대로여야(`clusters`와 1:1, 같은
        순서) `_scores_in_cluster_order()`의 위치 기반 매핑이 맞는다. 기존 상태는
        통째로 교체한다(누적 아님). `cluster_id_by_phrase`는 `member_cluster_ids()`로
        되돌려줄 멤버십(선택, `cache_store`가 같이 저장해둔 경우)."""
        self._clusters = list(clusters)
        self._cluster_id_by_phrase = (
            dict(cluster_id_by_phrase) if cluster_id_by_phrase is not None else None
        )
        if clusters:
            centroids = np.ascontiguousarray(centroids, dtype=np.float32)
            self._faiss_index = faiss.IndexFlatIP(centroids.shape[1])
            self._faiss_index.add(centroids)
        else:
            self._faiss_index = None

    def _scores_in_cluster_order(self, queries: np.ndarray) -> np.ndarray:
        """queries(N, dim) 각각에 대해 전체 클러스터와의 내적을 `self._clusters`와 같은
        순서로 재배열해 (N, ntotal) 로 반환한다.

        FAISS `search()`는 점수 내림차순으로 정렬해 (score, id) 쌍을 반환하므로,
        기존 동점 처리 로직(원래 순서에서 마지막 최댓값이 이긴다 — match()/match_many()
        문서 참고)을 그대로 재사용하려면 원래 클러스터 순서로 되돌려야 한다.
        """
        assert self._faiss_index is not None
        ntotal = self._faiss_index.ntotal
        scores, ids = self._faiss_index.search(queries, ntotal)
        full = np.full((queries.shape[0], ntotal), -np.inf, dtype=np.float32)
        rows = np.repeat(np.arange(queries.shape[0]), ntotal)
        full[rows, ids.reshape(-1)] = scores.reshape(-1)
        return full

    def match(self, phrase: str) -> PhraseCluster | None:
        """가장 가까운 클러스터를 찾는다. 문턱 미만이면 None(매칭 실패, DEC-005 정책).

        문구 하나짜리 호출 — 여러 문구를 매칭해야 하면 `match_many()`를 써야 한다.
        `embed_query()`가 호출마다 단건 배치로 모델을 부르는데, 이걸 반복 호출하면
        모델 디스패치 오버헤드가 실제 연산량을 압도한다(PR #57 실측, A: 실코퍼스에서
        문구 수천~수만 건을 이 메서드로 순회하니 5분에 14초치 연산만 진행돼 완주까지
        몇 시간 걸릴 것으로 추정됨).
        """
        from disclosure_lit.dictionary.cluster import CLUSTER_MIN_SCORE

        if not self._clusters:
            return None
        query = np.ascontiguousarray(embed_query(phrase), dtype=np.float32).reshape(1, -1)
        row = self._scores_in_cluster_order(query)[0]
        best_idx = -1
        best_score = CLUSTER_MIN_SCORE
        for c_idx, score in enumerate(row):
            if score >= best_score:
                best_score = score
                best_idx = c_idx
        return self._clusters[best_idx] if best_idx >= 0 else None

    def match_many(self, phrases: list[str]) -> list[PhraseCluster | None]:
        """여러 문구를 한 번에 배치로 매칭한다 — 결과·순서·판정 기준은 문구별로
        `match()`를 부른 것과 동일하다.

        `match()`를 문구마다 반복 호출하는 대신, `embed_texts()`로 전체를 한 번에
        배치 임베딩한 뒤(`cluster_phrases()`/`build()`가 이미 쓰는 것과 같은 패턴)
        FAISS 인덱스와 한 번에 비교한다 — 문구 수가 많을 때(수천~수만 건) `match()`
        반복 호출 대비 극적으로 빠르다(PR #57 실측 문제 해결).
        """
        from disclosure_lit.dictionary.cluster import CLUSTER_MIN_SCORE

        if not phrases:
            return []
        if not self._clusters:
            return [None] * len(phrases)

        queries = np.ascontiguousarray(embed_texts(phrases), dtype=np.float32)  # (N, dim)
        scores = self._scores_in_cluster_order(queries)  # (N, K), 원래 클러스터 순서

        results: list[PhraseCluster | None] = []
        for row in scores:
            # match()는 동점이면 나중 클러스터를 택한다(">=" 비교로 계속 덮어씀) —
            # np.argmax는 첫 동점만 반환하므로, 뒤집어서 첫 최댓값을 찾아 원래
            # 순서 기준 "마지막" 동점 인덱스를 구한다(match()와 동일한 판정 재현).
            best_idx = len(row) - 1 - int(np.argmax(row[::-1]))
            if float(row[best_idx]) >= CLUSTER_MIN_SCORE:
                results.append(self._clusters[best_idx])
            else:
                results.append(None)
        return results
