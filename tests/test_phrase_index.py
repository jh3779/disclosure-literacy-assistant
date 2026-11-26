"""L2b — PhraseClusterIndex 단위 테스트. build() + match()가 실제로 맞물리는지 확인.

임베딩은 결정론적인 가짜 벡터로 모킹한다. build()/match() 둘 다 내부적으로
dictionary.cluster를 함수 본문 안에서 import하므로(순환 참조 회피), 두 모듈 다
모킹해야 한다.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

import disclosure_lit.dictionary.cluster as cluster_module
import disclosure_lit.retrieval.phrase_index as phrase_index_module
from disclosure_lit.retrieval.phrase_index import PhraseClusterIndex

_FAKE_VOCAB = ["재무구조", "성장동력", "무관"]


def _fake_vector(text: str) -> np.ndarray:
    vec = np.array([1.0 if term in text else 0.0 for term in _FAKE_VOCAB], dtype=np.float32)
    norm = np.linalg.norm(vec)
    return vec / norm if norm > 0 else vec


def _fake_embed_texts(texts: list[str]) -> np.ndarray:
    return np.array([_fake_vector(t) for t in texts], dtype=np.float32)


def _fake_embed_query(text: str) -> np.ndarray:
    return _fake_vector(text)


@pytest.fixture(autouse=True)
def _mock_embeddings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cluster_module, "embed_texts", _fake_embed_texts)
    monkeypatch.setattr(phrase_index_module, "embed_texts", _fake_embed_texts)
    monkeypatch.setattr(phrase_index_module, "embed_query", _fake_embed_query)


class TestBuild:
    def test_build_returns_same_clusters_as_cluster_phrases(self) -> None:
        index = PhraseClusterIndex()
        clusters = index.build(["재무구조 개선", "재무구조 건전화", "성장동력 확보"])
        assert len(clusters) == 2

    def test_build_on_empty_list(self) -> None:
        index = PhraseClusterIndex()
        assert index.build([]) == []


class TestBuildDeterminism:
    """Issue #80 — build()의 그리디 클러스터링은 phrase 순서에 의존한다는 위험이
    한동안 docstring으로만 존재하고 테스트로 검증된 적이 없었다. 2026-09-17에
    `cluster_phrases()`가 처리 전에 입력을 정렬하도록 고쳐서, 이제는 순서와 무관하게
    같은 문구 집합이면 항상 같은 그룹 구성이 나온다 — 아래 테스트가 그 보장을
    검증한다. (`_outcome_labels()`가 그래도 같은 인덱스 인스턴스를 재사용해야 하는
    이유는 여전히 남아있다: cluster_id는 정렬된 발견 순으로 매겨지므로, 코퍼스
    자체가 달라지면(회사 추가/제거) 문구 집합이 달라져 cluster_id가 바뀔 수 있다.)"""

    # 단위원 위 각도로 코사인 유사도를 직접 제어한다: cos(38°)≈0.788(CLUSTER_MIN_SCORE=
    # 0.75 이상, P_A·P_B 묶임) vs cos(80°)≈0.174(P_A·P_C 안 묶임). P_B·P_C(42°)는
    # 0.743으로 문턱 바로 아래 — 2026-09-21 응집형 교체 전 40°였는데, 그러면 P_A·P_B와
    # P_B·P_C 거리가 정확히 동점이라 어느 쌍이 먼저 병합되는지가 scipy 내부 순서에
    # 달려 테스트가 알고리즘의 동점 규칙을 검사하는 꼴이 됐다.
    _ANGLE_DEG = {"P_A": 0, "P_B": 38, "P_C": 80}

    def _fake_embed_by_angle(self, texts: list[str]) -> np.ndarray:
        vectors = [
            [math.cos(math.radians(self._ANGLE_DEG[t])), math.sin(math.radians(self._ANGLE_DEG[t]))]
            for t in texts
        ]
        return np.array(vectors, dtype=np.float32)

    def test_build_is_reproducible_for_identical_input_order(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(cluster_module, "embed_texts", self._fake_embed_by_angle)
        phrases = ["P_A", "P_B", "P_C"]

        first = PhraseClusterIndex().build(list(phrases))
        second = PhraseClusterIndex().build(list(phrases))

        def mapping(clusters: list) -> list[tuple[str, str]]:
            return [(c.cluster_id, c.representative_phrase) for c in clusters]

        assert mapping(first) == mapping(second)

    def test_build_grouping_independent_of_phrase_order(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """2026-09-17 이전에는 순서만 바꿔도(먼저 등장하는 문구가 다르면) 어느 둘이
        한 클러스터로 묶이는지가 달라졌다 — `cluster_phrases()`가 처리 전에 입력을
        정렬하도록 고친 뒤로는(Issue #80 후속), 같은 문구 집합이면 호출부가 어떤
        순서로 넘기든 항상 같은 클러스터 구성이 나와야 한다."""
        monkeypatch.setattr(cluster_module, "embed_texts", self._fake_embed_by_angle)

        forward = PhraseClusterIndex().build(["P_A", "P_B", "P_C"])
        reversed_order = PhraseClusterIndex().build(["P_C", "P_B", "P_A"])

        def paired_representative(clusters: list) -> str:
            return next(c.representative_phrase for c in clusters if c.sample_count == 2)

        # 2026-09-21(응집형 교체 후): {P_A, P_B}가 한 클러스터, P_C는 홀로. 대표 문구는
        # centroid(19°)에 가장 가까운 멤버인데 P_A·P_B가 정확히 동거리라, 근사 동점 규칙에
        # 따라 정렬상 앞인 P_A가 된다 — 입력 순서와 무관하게 같아야 한다.
        assert paired_representative(forward) == "P_A"
        assert paired_representative(reversed_order) == "P_A"


class TestTrueCentroid:
    """2026-09-21 — build()가 대표 문구 재임베딩 근사 대신 멤버 평균 centroid를 인덱스에
    넣는다. 실측(후보 1,875개): 근사 centroid로는 클러스터링에 쓴 문구의 4.5%가 자기
    클러스터에 재매칭되지 않았고, 진짜 centroid로는 0%였다. 아래는 그 불변식이다."""

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

    def test_every_member_matches_its_own_cluster(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cluster_module, "embed_texts", self._embed_by_angle)
        monkeypatch.setattr(phrase_index_module, "embed_texts", self._embed_by_angle)
        index = PhraseClusterIndex()
        clusters = index.build(["P_A", "P_B", "P_C"])
        assert len(clusters) == 1

        matched = index.match_many(["P_A", "P_B", "P_C"])

        assert [c.cluster_id if c else None for c in matched] == ["pc-0000"] * 3
        assert index.member_cluster_ids() == {p: "pc-0000" for p in ["P_A", "P_B", "P_C"]}

    def test_index_holds_member_mean_not_a_single_member_vector(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(cluster_module, "embed_texts", self._embed_by_angle)
        index = PhraseClusterIndex()
        index.build(["P_A", "P_B", "P_C"])

        _, centroids = index.dump()

        vectors = self._embed_by_angle(["P_A", "P_B", "P_C"])
        expected = vectors.mean(axis=0)
        expected /= np.linalg.norm(expected)
        np.testing.assert_allclose(centroids[0], expected, atol=1e-6)


class TestBuildKeepsAllClustersRegardlessOfSampleSize:
    """2026-09-21(팀 리뷰 지적) — `build()`가 예전엔 `min_companies` 미만인 클러스터를
    통째로 버렸다. "이 표현이 뭔지 찾는 것"(매칭)과 "여러 회사 통계로 보여줄
    자격이 있는지"(신뢰도)는 다른 질문인데 하나로 묶여 있었다 — 표본이 적은 진짜
    표현까지 `match()`가 아예 못 찾게 됐었다. 이제 `build()`는 회사 수를 전혀 모르고
    (파라미터 자체가 없다) 그리디/응집형 클러스터링 결과를 그대로 다 남긴다. 회사 수
    기준 신뢰도 판정은 `dictionary/cluster_meaning.py::MIN_COMPANIES_FOR_MEANING`이
    별도로 맡는다(그쪽 테스트에서 검증)."""

    def test_single_occurrence_phrase_still_forms_a_matchable_cluster(self) -> None:
        index = PhraseClusterIndex()
        clusters = index.build(["재무구조 개선"])  # 딱 1개 문구 — 회사가 몇 곳인지는 모름

        assert len(clusters) == 1
        assert index.match("재무구조 관련 문의") is not None

    def test_build_no_longer_accepts_company_filtering_kwargs(self) -> None:
        """옛 시그니처(`companies_by_phrase`/`min_companies`)로 부르면 즉시 실패해야
        한다 — 조용히 무시되면 호출부가 "필터링되고 있다"고 착각할 수 있다."""
        index = PhraseClusterIndex()
        with pytest.raises(TypeError):
            index.build(["재무구조 개선"], min_companies=5)  # type: ignore[call-arg]


class TestMatch:
    def test_matches_phrase_in_known_cluster(self) -> None:
        index = PhraseClusterIndex()
        index.build(["선제적 재무구조 개선", "재무구조 건전화"])

        found = index.match("재무구조 관련 문구")

        assert found is not None
        assert found.cluster_id == "pc-0000"

    def test_returns_none_when_no_cluster_close_enough(self) -> None:
        index = PhraseClusterIndex()
        index.build(["재무구조 개선"])

        assert index.match("완전히 다른 이야기") is None

    def test_returns_none_before_build_called(self) -> None:
        index = PhraseClusterIndex()
        assert index.match("아무 문장") is None


class TestMatchMany:
    """배치 매칭 — PR #57 실측(A): match()를 문구마다 반복 호출하면 embed_query()
    단건 호출 오버헤드가 누적돼 실코퍼스 규모(문구 수천~수만 건)에서 완주까지
    몇 시간이 걸렸다. match_many()는 embed_texts()로 한 번에 배치 임베딩한다."""

    def test_empty_phrases_returns_empty_list(self) -> None:
        index = PhraseClusterIndex()
        index.build(["재무구조 개선"])
        assert index.match_many([]) == []

    def test_returns_none_list_before_build_called(self) -> None:
        index = PhraseClusterIndex()
        assert index.match_many(["아무 문장", "다른 문장"]) == [None, None]

    def test_matches_each_phrase_independently(self) -> None:
        index = PhraseClusterIndex()
        index.build(["선제적 재무구조 개선", "성장동력 확보 계획"])

        results = index.match_many(["재무구조 관련 문구", "완전히 다른 이야기", "성장동력 논의"])

        assert results[0] is not None and results[0].cluster_id == "pc-0000"
        assert results[1] is None
        assert results[2] is not None and results[2].cluster_id == "pc-0001"

    def test_matches_same_results_as_repeated_match_calls(self) -> None:
        """match_many()가 배치로 처리해도, match()를 문구마다 호출한 것과 결과가
        완전히 같아야 한다(동점 판정 기준 포함) — 순수 성능 최적화이지 동작 변경이
        아니라는 걸 보장하는 회귀 테스트."""
        index = PhraseClusterIndex()
        index.build(["재무구조 개선", "성장동력 확보", "무관한 이야기"])
        phrases = ["재무구조 논의", "성장동력 계획", "완전히 새로운 내용", "재무구조 성장동력"]

        individually = [index.match(p) for p in phrases]
        batched = index.match_many(phrases)

        assert [c.cluster_id if c else None for c in batched] == [
            c.cluster_id if c else None for c in individually
        ]
