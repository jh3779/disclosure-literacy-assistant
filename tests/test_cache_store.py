"""cache_store.py 단위 테스트 — 콜드스타트 디스크 캐시.

핵심으로 검증할 것: (1) company 번들 왕복이 메모리 버전과 동일한 결과를 내는지,
(2) phrase 번들이 절대 절반만(클러스터만 또는 라벨만) 로드되지 않는지 — cluster_id
비결정성 위험을 막는 원자성 보장이 이 모듈의 존재 이유다(cache_store.py 모듈 docstring
참고), (3) 지문이 바뀌면(코퍼스 변경 등) 캐시가 자연스럽게 미스되는지.
"""

from __future__ import annotations

import faiss
import numpy as np
import pytest

import disclosure_lit.cache_store as cache_store
from disclosure_lit.dart.ingest import DisclosureSection
from disclosure_lit.dictionary.stats import OutcomeLabel
from disclosure_lit.retrieval.company_index import CompanyDisclosureIndex
from disclosure_lit.retrieval.phrase_index import PhraseCluster, PhraseClusterIndex

_SECTION_A = DisclosureSection(
    rcept_no="R1", corp_code="C1", form_type="유상증자", section_title="t1", text="본문1"
)
_SECTION_B = DisclosureSection(
    rcept_no="R2", corp_code="C2", form_type="MOU", section_title="t2", text="본문2"
)


def _faiss_of(*vectors: list[float]) -> faiss.IndexFlatIP:
    """테스트 전용 헬퍼 — corp_code 하나짜리 FAISS 인덱스를 즉석에서 만든다
    (`CompanyDisclosureIndex._faiss_by_corp`의 내부 표현, 2026-09-18 FAISS 도입)."""
    array = np.array(vectors, dtype=np.float32)
    index = faiss.IndexFlatIP(array.shape[1])
    index.add(array)
    return index


class TestCompanyFingerprint:
    def test_same_sections_give_same_fingerprint(self) -> None:
        fp1 = cache_store.company_fingerprint([_SECTION_A, _SECTION_B])
        fp2 = cache_store.company_fingerprint([_SECTION_B, _SECTION_A])  # 순서 무관
        assert fp1 == fp2

    def test_different_corpus_gives_different_fingerprint(self) -> None:
        fp1 = cache_store.company_fingerprint([_SECTION_A])
        fp2 = cache_store.company_fingerprint([_SECTION_A, _SECTION_B])
        assert fp1 != fp2


class TestCompanyIndexRoundTrip:
    def test_load_missing_cache_returns_none(self, tmp_path) -> None:
        assert cache_store.load_company_index(tmp_path, "nonexistent") is None

    def test_save_then_load_reproduces_search_results(self, tmp_path) -> None:
        index = CompanyDisclosureIndex()
        index._by_corp["C1"] = [_SECTION_A]
        index._faiss_by_corp["C1"] = _faiss_of([1.0, 0.0])
        index._by_corp["C2"] = [_SECTION_B]
        index._faiss_by_corp["C2"] = _faiss_of([0.0, 1.0])

        cache_store.save_company_index(tmp_path, "fp1", index)
        loaded = cache_store.load_company_index(tmp_path, "fp1")

        assert loaded is not None
        assert loaded._by_corp["C1"] == [_SECTION_A]
        assert loaded._by_corp["C2"] == [_SECTION_B]
        c1_faiss = loaded._faiss_by_corp["C1"]
        c2_faiss = loaded._faiss_by_corp["C2"]
        np.testing.assert_array_equal(c1_faiss.reconstruct_n(0, c1_faiss.ntotal), [[1.0, 0.0]])
        np.testing.assert_array_equal(c2_faiss.reconstruct_n(0, c2_faiss.ntotal), [[0.0, 1.0]])

    def test_save_is_idempotent_for_same_fingerprint(self, tmp_path) -> None:
        index = CompanyDisclosureIndex()
        index._by_corp["C1"] = [_SECTION_A]
        index._faiss_by_corp["C1"] = _faiss_of([1.0, 0.0])

        cache_store.save_company_index(tmp_path, "fp1", index)
        cache_store.save_company_index(tmp_path, "fp1", index)  # 두 번째 호출도 에러 없이 스킵

        loaded = cache_store.load_company_index(tmp_path, "fp1")
        assert loaded is not None

    def test_corrupted_cache_falls_back_to_none(self, tmp_path) -> None:
        bundle_dir = tmp_path / "company" / "fp1"
        bundle_dir.mkdir(parents=True)
        (bundle_dir / "sections.jsonl").write_text("이건 json이 아님\n", encoding="utf-8")
        # vectors.npy 없음 — 읽기 도중 실패해야 한다

        assert cache_store.load_company_index(tmp_path, "fp1") is None


class TestPhraseBundleAtomicity:
    """가장 중요한 계약: 클러스터와 라벨은 항상 함께 저장/로드되고, 절반만 로드되는
    경우는 절대 없어야 한다(cluster_id 비결정성으로 인한 조용한 데이터 오염 방지)."""

    def _make_index(self) -> PhraseClusterIndex:
        index = PhraseClusterIndex()
        index.load(
            [
                PhraseCluster(
                    cluster_id="pc-0000", representative_phrase="재무구조 개선", sample_count=2
                )
            ],
            np.array([[1.0, 0.0]], dtype=np.float32),
        )
        return index

    def test_load_missing_cache_returns_none(self, tmp_path) -> None:
        assert cache_store.load_phrase_bundle(tmp_path, "nonexistent") is None

    def test_save_then_load_reproduces_clusters_and_labels_together(self, tmp_path) -> None:
        index = self._make_index()
        labels = [OutcomeLabel(cluster_id="pc-0000", corp_code="C1", outcome_label="유상증자")]

        cache_store.save_phrase_bundle(tmp_path, "fp1", index, labels)
        result = cache_store.load_phrase_bundle(tmp_path, "fp1")

        assert result is not None
        loaded_index, loaded_labels = result
        clusters, centroids = loaded_index.dump()
        assert [c.cluster_id for c in clusters] == ["pc-0000"]
        np.testing.assert_array_equal(centroids, [[1.0, 0.0]])
        assert loaded_labels == labels

    def test_resaving_same_fingerprint_is_skipped_by_default(self, tmp_path) -> None:
        """기본값은 "같은 지문 = 같은 내용"이라 다시 쓰지 않는다 — 이 스킵 동작 자체가
        아래 replace_existing 테스트의 대조군이다."""
        index = self._make_index()
        cache_store.save_phrase_bundle(tmp_path, "fp1", index, [])
        labels = [OutcomeLabel(cluster_id="pc-0000", corp_code="C1", outcome_label="유상증자")]

        cache_store.save_phrase_bundle(tmp_path, "fp1", index, labels)

        result = cache_store.load_phrase_bundle(tmp_path, "fp1")
        assert result is not None
        assert result[1] == []  # 스킵됐으므로 디스크는 그대로

    def test_replace_existing_actually_updates_empty_label_bundle(self, tmp_path) -> None:
        """2026-09-22 PR #112 리뷰(@9abin) — demo/app.py가 라벨을 빈 리스트로 저장하기
        시작하면서 "같은 지문인데 내용이 다른" 번들이 생겼다. 평가 러너가 라벨을 계산해
        채워 넣을 때 교체하지 않으면 로그만 '저장 완료'로 나오고 디스크는 계속 비어 있어,
        다음 실행에서 수 분~수십 분짜리 매칭을 반복한다."""
        index = self._make_index()
        cache_store.save_phrase_bundle(tmp_path, "fp1", index, [])
        labels = [OutcomeLabel(cluster_id="pc-0000", corp_code="C1", outcome_label="유상증자")]

        cache_store.save_phrase_bundle(tmp_path, "fp1", index, labels, replace_existing=True)

        result = cache_store.load_phrase_bundle(tmp_path, "fp1")
        assert result is not None
        loaded_index, loaded_labels = result
        assert loaded_labels == labels
        # 교체가 클러스터 쪽을 망가뜨리지 않았는지도 같이 본다(번들은 항상 통째로 유효해야 함)
        assert [c.cluster_id for c in loaded_index.dump()[0]] == ["pc-0000"]

    def test_replace_existing_leaves_no_temp_files_behind(self, tmp_path) -> None:
        """교체는 임시 파일을 거치므로, 끝나고 나면 지문 디렉터리 하나만 남고 그
        안에도 `.tmp-*` 찌꺼기가 없어야 한다 — 평가를 돌릴 때마다 쌓이면 안 된다."""
        index = self._make_index()
        cache_store.save_phrase_bundle(tmp_path, "fp1", index, [])
        labels = [OutcomeLabel(cluster_id="pc-0000", corp_code="C1", outcome_label="유상증자")]

        cache_store.save_phrase_bundle(tmp_path, "fp1", index, labels, replace_existing=True)

        assert [p.name for p in (tmp_path / "phrase").iterdir()] == ["fp1"]
        leftovers = [p.name for p in (tmp_path / "phrase" / "fp1").iterdir() if ".tmp-" in p.name]
        assert leftovers == []

    def test_replace_existing_does_not_rewrite_clusters(self, tmp_path) -> None:
        """라벨 보충은 clusters/centroids를 건드리지 않는다 — 같은 지문이면 클러스터링이
        결정적이라 내용이 이미 같고, 안 건드려야 디렉터리 교체(경쟁 상태의 원인)를
        피할 수 있다(2026-09-22 리뷰 @9abin)."""
        index = self._make_index()
        cache_store.save_phrase_bundle(tmp_path, "fp1", index, [])
        clusters_path = tmp_path / "phrase" / "fp1" / "clusters.jsonl"
        before = clusters_path.stat().st_mtime_ns, clusters_path.read_bytes()
        labels = [OutcomeLabel(cluster_id="pc-0000", corp_code="C1", outcome_label="유상증자")]

        cache_store.save_phrase_bundle(tmp_path, "fp1", index, labels, replace_existing=True)

        assert (clusters_path.stat().st_mtime_ns, clusters_path.read_bytes()) == before

    def test_replace_existing_refuses_to_empty_existing_labels(self, tmp_path) -> None:
        """`replace_existing=True`는 라벨을 **채우는** 용도다 — 빈 라벨로 교체를
        요청받으면 아무것도 하지 않아야 한다. 이 가드가 없으면 라벨 계산에 실패한
        실행이 멀쩡한 캐시를 지운다."""
        index = self._make_index()
        labels = [OutcomeLabel(cluster_id="pc-0000", corp_code="C1", outcome_label="유상증자")]
        cache_store.save_phrase_bundle(tmp_path, "fp1", index, labels)

        cache_store.save_phrase_bundle(tmp_path, "fp1", index, [], replace_existing=True)

        result = cache_store.load_phrase_bundle(tmp_path, "fp1")
        assert result is not None
        assert result[1] == labels

    def test_default_writer_never_erases_labels_committed_during_its_write(
        self, tmp_path, monkeypatch
    ) -> None:
        """2026-09-22 리뷰(@9abin) 회귀 — 데모(빈 라벨, replace_existing=False)가 쓰는
        동안 평가가 먼저 라벨 캐시를 완성하면, 뒤늦게 끝난 데모가 그걸 지우면 안 된다.

        타이밍(sleep)이 아니라 `threading.Event`로 "데모가 쓰기 도중에 멈춰 있는 동안
        평가가 끝난다"는 순서를 강제해 결정론적으로 검증한다."""
        import threading
        from concurrent.futures import ThreadPoolExecutor

        index = self._make_index()
        labels = [OutcomeLabel(cluster_id="pc-0000", corp_code="C1", outcome_label="유상증자")]
        original_write = cache_store._write_jsonl
        demo_writing = threading.Event()
        allow_demo_commit = threading.Event()

        def _pause_demo(path, rows):
            original_write(path, rows)
            is_demo = threading.current_thread().name.startswith("demo")
            if is_demo and path.name == "outcome_labels.jsonl":
                demo_writing.set()
                assert allow_demo_commit.wait(timeout=10)

        monkeypatch.setattr(cache_store, "_write_jsonl", _pause_demo)
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="demo") as executor:
            pending = executor.submit(cache_store.save_phrase_bundle, tmp_path, "fp1", index, [])
            try:
                assert demo_writing.wait(timeout=10)
                cache_store.save_phrase_bundle(
                    tmp_path, "fp1", index, labels, replace_existing=True
                )
            finally:
                allow_demo_commit.set()
            pending.result(timeout=10)

        result = cache_store.load_phrase_bundle(tmp_path, "fp1")
        assert result is not None
        assert result[1] == labels, "기본 저장이 이미 완성된 라벨을 지웠다"

    def test_replace_existing_keeps_old_bundle_when_write_fails(
        self, tmp_path, monkeypatch
    ) -> None:
        """교체 도중 실패해도 기존 캐시가 사라지면 안 된다 — 교체 실패가 '캐시 소실'로
        번지면 다음 실행이 처음부터 다시 빌드해야 한다."""
        index = self._make_index()
        labels = [OutcomeLabel(cluster_id="pc-0000", corp_code="C1", outcome_label="유상증자")]
        cache_store.save_phrase_bundle(tmp_path, "fp1", index, labels)

        def _boom(*args, **kwargs):
            raise OSError("디스크 꽉 참")

        monkeypatch.setattr(cache_store, "_write_jsonl", _boom)
        # save_phrase_bundle은 쓰기 실패를 경고만 남기고 삼킨다(캐시 없이 계속 진행).
        # 빈 라벨은 위 가드에 먼저 걸리므로, 쓰기 실패 경로를 타려면 라벨이 있어야 한다.
        newer = [OutcomeLabel(cluster_id="pc-0000", corp_code="C2", outcome_label="감자")]
        cache_store.save_phrase_bundle(tmp_path, "fp1", index, newer, replace_existing=True)

        monkeypatch.undo()
        result = cache_store.load_phrase_bundle(tmp_path, "fp1")
        assert result is not None
        assert result[1] == labels  # 원래 라벨 그대로 살아있다

    def test_missing_outcome_labels_file_invalidates_whole_bundle(self, tmp_path) -> None:
        """clusters.jsonl/centroids.npy는 멀쩡해도 outcome_labels.jsonl이 없으면(예:
        쓰기 도중 손상) 클러스터만 부분적으로 재사용하지 않고 번들 전체를 버려야 한다."""
        index = self._make_index()
        cache_store.save_phrase_bundle(tmp_path, "fp1", index, [])
        (tmp_path / "phrase" / "fp1" / "outcome_labels.jsonl").unlink()

        assert cache_store.load_phrase_bundle(tmp_path, "fp1") is None

    def test_corrupted_clusters_file_invalidates_whole_bundle(self, tmp_path) -> None:
        index = self._make_index()
        cache_store.save_phrase_bundle(tmp_path, "fp1", index, [])
        (tmp_path / "phrase" / "fp1" / "clusters.jsonl").write_text("깨짐", encoding="utf-8")

        assert cache_store.load_phrase_bundle(tmp_path, "fp1") is None

    def test_member_map_round_trips_when_present(self, tmp_path) -> None:
        """2026-09-21 — build()가 남긴 정확한 멤버십(문구→cluster_id)도 번들에 같이 저장한다."""
        index = PhraseClusterIndex()
        members = {"재무구조 개선": "pc-0000", "재무구조 건전화": "pc-0000"}
        index.load(
            [
                PhraseCluster(
                    cluster_id="pc-0000", representative_phrase="재무구조 개선", sample_count=2
                )
            ],
            np.array([[1.0, 0.0]], dtype=np.float32),
            cluster_id_by_phrase=members,
        )

        cache_store.save_phrase_bundle(tmp_path, "fp1", index, [])
        result = cache_store.load_phrase_bundle(tmp_path, "fp1")

        assert result is not None
        assert result[0].member_cluster_ids() == members

    def test_bundle_without_member_map_still_loads(self, tmp_path) -> None:
        index = self._make_index()  # load()에 멤버십을 안 넘김 → None

        cache_store.save_phrase_bundle(tmp_path, "fp1", index, [])

        assert not (tmp_path / "phrase" / "fp1" / "members.json").exists()
        result = cache_store.load_phrase_bundle(tmp_path, "fp1")
        assert result is not None
        assert result[0].member_cluster_ids() is None


class TestPhraseFingerprint:
    def test_different_post_event_labels_file_changes_fingerprint(self, tmp_path) -> None:
        fp_before = cache_store.phrase_fingerprint([_SECTION_A], tmp_path)
        (tmp_path / "post_event_labels.jsonl").write_text(
            '{"rcept_no": "R1", "outcome_events": ["유상증자"]}\n', encoding="utf-8"
        )
        fp_after = cache_store.phrase_fingerprint([_SECTION_A], tmp_path)
        assert fp_before != fp_after


class TestPruneOldGenerations:
    def test_old_fingerprint_directory_is_removed_after_new_save(self, tmp_path) -> None:
        index = CompanyDisclosureIndex()
        index._by_corp["C1"] = [_SECTION_A]
        index._faiss_by_corp["C1"] = _faiss_of([1.0, 0.0])

        cache_store.save_company_index(tmp_path, "old-fp", index)
        cache_store.save_company_index(tmp_path, "new-fp", index)

        assert not (tmp_path / "company" / "old-fp").exists()
        assert (tmp_path / "company" / "new-fp").exists()


@pytest.fixture
def _dummy_settings(monkeypatch: pytest.MonkeyPatch):
    """지문 계산이 settings/embed_module 전역 상태를 읽으므로, 값이 바뀌면 지문도
    바뀌는지 확인하려면 이 전역을 건드릴 수 있어야 한다."""
    import disclosure_lit.embed as embed_module

    original = embed_module.MAX_EMBED_CHARS
    yield embed_module
    embed_module.MAX_EMBED_CHARS = original


class TestEmbedConfigChangesFingerprint:
    def test_max_embed_chars_change_invalidates_company_cache(self, _dummy_settings) -> None:
        fp_before = cache_store.company_fingerprint([_SECTION_A])
        _dummy_settings.MAX_EMBED_CHARS = _dummy_settings.MAX_EMBED_CHARS + 1
        fp_after = cache_store.company_fingerprint([_SECTION_A])
        assert fp_before != fp_after


class TestCompanySectionFingerprint:
    """2026-09-18 — 회사별 지연 로딩 캐시 지문. `company_fingerprint()`(코퍼스 전체)와
    달리 corp_code 하나 + 그 회사 자신의 섹션만 본다."""

    def test_same_company_same_sections_give_same_fingerprint(self) -> None:
        fp1 = cache_store.company_section_fingerprint("C1", [_SECTION_A])
        fp2 = cache_store.company_section_fingerprint("C1", [_SECTION_A])
        assert fp1 == fp2

    def test_different_corp_code_gives_different_fingerprint_even_with_same_sections(
        self,
    ) -> None:
        """corp_code 자체가 지문 재료라, 같은 섹션 내용이라도 다른 회사로 잘못
        찍히면(호출부 버그) 지문이 달라져 자연스럽게 캐시 미스가 된다."""
        fp1 = cache_store.company_section_fingerprint("C1", [_SECTION_A])
        fp2 = cache_store.company_section_fingerprint("C2", [_SECTION_A])
        assert fp1 != fp2


class TestCompanySectionVectorsRoundTrip:
    """2026-09-18 — 회사별 지연 로딩 캐시 왕복. `company/` 전체 번들과 별개로
    `company_lazy/<corp_code>/`에 독립적으로 저장·로드된다."""

    def test_load_missing_cache_returns_none(self, tmp_path) -> None:
        assert cache_store.load_company_section_vectors(tmp_path, "C1", "fp1") is None

    def test_save_then_load_round_trips(self, tmp_path) -> None:
        vectors = np.array([[1.0, 0.0]], dtype=np.float32)

        cache_store.save_company_section_vectors(tmp_path, "C1", "fp1", [_SECTION_A], vectors)
        loaded = cache_store.load_company_section_vectors(tmp_path, "C1", "fp1")

        assert loaded is not None
        loaded_sections, loaded_vectors = loaded
        assert loaded_sections == [_SECTION_A]
        np.testing.assert_array_equal(loaded_vectors, vectors)

    def test_two_companies_do_not_collide(self, tmp_path) -> None:
        """한 회사를 캐싱해도 다른 회사의 캐시는 안 건드려야 한다 — 지연 로딩의
        핵심 전제(_prune_old_generations가 회사별 디렉터리 안에서만 돈다)."""
        vectors_a = np.array([[1.0, 0.0]], dtype=np.float32)
        vectors_b = np.array([[0.0, 1.0]], dtype=np.float32)

        cache_store.save_company_section_vectors(tmp_path, "C1", "fp1", [_SECTION_A], vectors_a)
        cache_store.save_company_section_vectors(tmp_path, "C2", "fp1", [_SECTION_B], vectors_b)

        loaded_a = cache_store.load_company_section_vectors(tmp_path, "C1", "fp1")
        loaded_b = cache_store.load_company_section_vectors(tmp_path, "C2", "fp1")

        assert loaded_a is not None and loaded_a[0] == [_SECTION_A]
        assert loaded_b is not None and loaded_b[0] == [_SECTION_B]

    def test_new_fingerprint_prunes_only_that_companys_old_generation(self, tmp_path) -> None:
        vectors = np.array([[1.0, 0.0]], dtype=np.float32)
        cache_store.save_company_section_vectors(tmp_path, "C1", "old-fp", [_SECTION_A], vectors)
        cache_store.save_company_section_vectors(tmp_path, "C2", "fp1", [_SECTION_B], vectors)

        cache_store.save_company_section_vectors(tmp_path, "C1", "new-fp", [_SECTION_A], vectors)

        assert not (tmp_path / "company_lazy" / "C1" / "old-fp").exists()
        assert (tmp_path / "company_lazy" / "C1" / "new-fp").exists()
        assert (tmp_path / "company_lazy" / "C2" / "fp1").exists()  # 다른 회사는 안 건드림

    def test_corrupted_cache_falls_back_to_none(self, tmp_path) -> None:
        bundle_dir = tmp_path / "company_lazy" / "C1" / "fp1"
        bundle_dir.mkdir(parents=True)
        (bundle_dir / "sections.jsonl").write_text("이건 json이 아님\n", encoding="utf-8")

        assert cache_store.load_company_section_vectors(tmp_path, "C1", "fp1") is None


class TestClusterMeaningsCache:
    """`_cluster_meanings()`의 디스크 캐시 — 2026-09-25 응답시간 측정 후속.

    측정(`docs/testing/response-time.md`)에서 서버를 새로 띄울 때마다 이 계산에 약 4분
    20초가 드는 것이 드러났다. 코드 주석은 "수십 초라 무시할 수준"을 근거로 캐싱하지
    않았는데 그 전제가 더는 맞지 않는다.
    """

    def _meanings(self) -> dict:
        from disclosure_lit.dictionary.cluster_meaning import ClusterMeaning

        return {
            "pc-0001": ClusterMeaning(
                cluster_id="pc-0001",
                representative_phrase="선제적 재무구조 개선을 위해",
                dominant_form_type="유상증자",
                purity=0.82,
                occurrence_count=17,
                company_count=6,
                is_reliable=True,
            )
        }

    def test_round_trip_preserves_every_field(self, tmp_path) -> None:
        saved = self._meanings()
        cache_store.save_cluster_meanings(tmp_path, "fp1", saved)

        loaded = cache_store.load_cluster_meanings(tmp_path, "fp1")

        assert loaded == saved

    def test_missing_cache_returns_none(self, tmp_path) -> None:
        assert cache_store.load_cluster_meanings(tmp_path, "fp1") is None

    def test_other_fingerprint_is_not_reused(self, tmp_path) -> None:
        cache_store.save_cluster_meanings(tmp_path, "fp1", self._meanings())

        assert cache_store.load_cluster_meanings(tmp_path, "fp2") is None

    def test_corrupted_cache_falls_back_to_none(self, tmp_path) -> None:
        cache_store.save_cluster_meanings(tmp_path, "fp1", self._meanings())
        path = next((tmp_path / "cluster_meanings").glob("*"))
        path.write_text("이건 json이 아님\n", encoding="utf-8")

        assert cache_store.load_cluster_meanings(tmp_path, "fp1") is None

    def test_empty_result_is_cached_as_empty_not_missing(self, tmp_path) -> None:
        """계산 결과가 빈 dict인 것과 캐시가 없는 것은 다르다 — 섞이면 매번 재계산한다."""
        cache_store.save_cluster_meanings(tmp_path, "fp1", {})

        assert cache_store.load_cluster_meanings(tmp_path, "fp1") == {}

    def test_fingerprint_changes_when_meaning_tunables_change(self, tmp_path, monkeypatch) -> None:
        """순도 문턱 같은 값을 조정하면 기존 캐시를 재사용하면 안 된다.

        `phrase_fingerprint`가 클러스터링 상수를 지문에 넣는 것과 같은 이유다 — 튜닝한
        값이 지문에 안 잡히면 stale 캐시가 조용히 재사용된다.
        """
        from disclosure_lit.dictionary import cluster_meaning as meaning_module

        before = cache_store.cluster_meanings_fingerprint([_SECTION_A], tmp_path)
        monkeypatch.setattr(meaning_module, "MIN_PURITY_TO_DISPLAY", 0.9)
        after = cache_store.cluster_meanings_fingerprint([_SECTION_A], tmp_path)

        assert before != after

    def test_fingerprint_follows_the_cluster_bundle(self, tmp_path) -> None:
        """코퍼스·클러스터링이 바뀌면 의미도 다시 계산해야 한다."""
        before = cache_store.cluster_meanings_fingerprint([_SECTION_A], tmp_path)
        (tmp_path / "post_event_labels.jsonl").write_text(
            '{"rcept_no": "R1", "outcome_events": ["유상증자"]}\n', encoding="utf-8"
        )
        after = cache_store.cluster_meanings_fingerprint([_SECTION_A], tmp_path)

        assert before != after
