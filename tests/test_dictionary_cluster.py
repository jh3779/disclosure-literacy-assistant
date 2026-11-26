"""L2b — cluster_phrases 단위 테스트. 상투구 클러스터링.

임베딩은 결정론적인 가짜 벡터로 모킹한다(test_company_disclosure_index.py와 같은
원칙) — 실제 dragonkue/BGE-m3-ko는 무겁고 CI 기본 실행에는 optional extra라 안 쓴다.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

import disclosure_lit.dictionary.cluster as cluster_module
from disclosure_lit.dictionary.cluster import (
    CLUSTER_MIN_SCORE,
    cluster_phrases,
    cluster_phrases_detailed,
)

_FAKE_VOCAB = ["재무구조", "성장동력", "인력", "무관"]


def _fake_vector(text: str) -> np.ndarray:
    vec = np.array([1.0 if term in text else 0.0 for term in _FAKE_VOCAB], dtype=np.float32)
    norm = np.linalg.norm(vec)
    return vec / norm if norm > 0 else vec


def _fake_embed_texts(texts: list[str]) -> np.ndarray:
    return np.array([_fake_vector(t) for t in texts], dtype=np.float32)


@pytest.fixture(autouse=True)
def _mock_embeddings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cluster_module, "embed_texts", _fake_embed_texts)


class TestClusterPhrases:
    def test_empty_input_returns_empty(self) -> None:
        assert cluster_phrases([]) == []

    def test_blank_phrases_are_skipped(self) -> None:
        assert cluster_phrases(["", "   "]) == []

    def test_similar_phrases_merge_into_one_cluster(self) -> None:
        clusters = cluster_phrases(["선제적 재무구조 개선", "재무구조 건전화 방안"])

        assert len(clusters) == 1
        assert clusters[0].sample_count == 2
        assert clusters[0].representative_phrase == "선제적 재무구조 개선"

    def test_dissimilar_phrases_form_separate_clusters(self) -> None:
        clusters = cluster_phrases(["재무구조 개선", "성장동력 확보", "인력 효율화"])

        assert len(clusters) == 3
        assert all(c.sample_count == 1 for c in clusters)

    def test_cluster_ids_are_stable_and_ordered(self) -> None:
        clusters = cluster_phrases(["재무구조", "성장동력"])
        assert [c.cluster_id for c in clusters] == ["pc-0000", "pc-0001"]

    def test_threshold_boundary_exact_match_merges(self) -> None:
        """코사인 유사도가 문턱값과 정확히 같으면(부동소수 오차 감안) 병합돼야 한다."""
        clusters = cluster_phrases(["재무구조", "재무구조"])  # 완전 동일 문구 → 유사도 1.0
        assert len(clusters) == 1
        assert CLUSTER_MIN_SCORE < 1.0  # 문턱값 자체가 1.0보다 낮다는 전제 확인


class TestAgglomerativeClustering:
    """2026-09-21 — 그리디 단일 패스를 average-linkage 응집형으로 교체. 단위원 위 각도로
    코사인 유사도를 직접 제어한다(`test_phrase_index.py::TestBuildDeterminism`과 같은
    방식)."""

    _ANGLE_DEG = {"P_A": 0, "P_B": 6, "P_C": 42}

    def _embed_by_angle(self, texts: list[str]) -> np.ndarray:
        return np.array(
            [
                [
                    math.cos(math.radians(self._ANGLE_DEG[t])),
                    math.sin(math.radians(self._ANGLE_DEG[t])),
                ]
                for t in texts
            ],
            dtype=np.float32,
        )

    def test_representative_is_member_closest_to_centroid(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """대표 문구는 정렬상 첫 멤버(P_A)가 아니라 centroid(약 16°)에 가장 가까운 멤버
        (P_B, 6°)여야 한다. 0°·6°·42°는 average linkage로 한 클러스터가 된다: P_A-P_B
        거리 0.006 → 병합, 그 뒤 {P_A,P_B}-P_C 평균 거리 (0.257+0.191)/2=0.224 <= 0.25."""
        monkeypatch.setattr(cluster_module, "embed_texts", self._embed_by_angle)

        clusters = cluster_phrases(["P_A", "P_B", "P_C"])

        assert len(clusters) == 1
        assert clusters[0].sample_count == 3
        assert clusters[0].representative_phrase == "P_B"

    def test_detailed_result_exposes_true_centroid(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cluster_module, "embed_texts", self._embed_by_angle)

        result = cluster_phrases_detailed(["P_C", "P_A", "P_B"])

        assert result.phrases == ["P_A", "P_B", "P_C"]  # 정렬됨
        assert result.members == [[0, 1, 2]]
        vectors = self._embed_by_angle(result.phrases)
        expected = vectors.mean(axis=0)
        expected /= np.linalg.norm(expected)
        np.testing.assert_allclose(result.centroids[0], expected, atol=1e-6)
        # 어떤 멤버 벡터와도 같지 않다 — 대표 문구 재임베딩 근사와 구분되는 지점
        assert not any(np.allclose(result.centroids[0], v, atol=1e-6) for v in vectors)

    def test_far_apart_phrases_do_not_chain_through_a_middle_one(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """0°·40°·80°: P_A-P_B, P_B-P_C는 각각 문턱 안이지만(0.234) P_A-P_C는 아니다(0.826).
        average linkage는 {P_A,P_B}와 P_C의 평균 거리(0.53)로 판단해 셋을 한 덩어리로
        엮지 않는다."""
        monkeypatch.setattr(
            cluster_module,
            "embed_texts",
            lambda texts: np.array(
                [
                    [math.cos(math.radians(a)), math.sin(math.radians(a))]
                    for a in ({"P_A": 0, "P_B": 40, "P_C": 80}[t] for t in texts)
                ],
                dtype=np.float32,
            ),
        )

        clusters = cluster_phrases(["P_A", "P_B", "P_C"])

        assert sorted(c.sample_count for c in clusters) == [1, 2]
