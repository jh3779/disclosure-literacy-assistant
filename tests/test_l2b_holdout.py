"""disclosure_lit.eval.l2b_holdout 단위 테스트 — Issue #66 held-out 검증. 임베딩 불필요
(재클러스터링을 안 하는 설계라 OutcomeLabel만 다루면 된다)."""

from __future__ import annotations

import pytest

from disclosure_lit.dictionary.stats import OutcomeLabel, register_outcome_labels


@pytest.fixture(autouse=True)
def _reset_labels() -> None:
    register_outcome_labels([])


class TestBuildLabelsCacheReplenishment:
    """2026-09-22 PR #112 리뷰(@9abin) 회귀 — 빈 라벨 캐시를 채운 뒤 **두 번째 실행에서
    다시 계산하지 않는가**. 이게 깨지면 평가를 돌릴 때마다 수 분~수십 분짜리 매칭을
    반복하는데, 로그는 매번 '캐시 저장 완료'라고 나와서 알아채기 어렵다."""

    def _prepare(self, tmp_path, monkeypatch) -> list:
        import numpy as np

        from disclosure_lit import cache_store
        from disclosure_lit.dart.ingest import DisclosureSection
        from disclosure_lit.dictionary import candidates as candidates_module
        from disclosure_lit.dictionary import stats as stats_module
        from disclosure_lit.retrieval.phrase_index import PhraseCluster, PhraseClusterIndex

        sections = [
            DisclosureSection(
                rcept_no="R1",
                corp_code="C1",
                form_type="기타",
                section_title="제목",
                text="재무구조 개선을 검토 중입니다.",
            )
        ]
        index = PhraseClusterIndex()
        index.load(
            [
                PhraseCluster(
                    cluster_id="pc-0000", representative_phrase="재무구조 개선", sample_count=2
                )
            ],
            np.array([[1.0, 0.0]], dtype=np.float32),
        )
        # 데모가 저장해둔 "라벨 없는" 번들을 재현한다
        cache_store.save_phrase_bundle(tmp_path, "fp1", index, [])

        monkeypatch.setattr(candidates_module, "load_all_local_sections", lambda _: sections)
        monkeypatch.setattr(cache_store, "phrase_fingerprint", lambda *_: "fp1")
        monkeypatch.setattr(stats_module, "load_post_event_labels", lambda _: {"R1": ["유상증자"]})

        built = [OutcomeLabel("pc-0000", "C1", "유상증자")]
        calls: list[int] = []

        def _fake_build_outcome_labels(sections, post_events, index):
            calls.append(1)
            return built

        import disclosure_lit.eval.l2b_holdout as module

        # 2026-09-24: 라벨 생성이 `dictionary/outcome_labels.py`로 옮겨가면서, 가로채는
        # 대상도 `demo.app`이 아니라 이 모듈이 import해둔 이름으로 바뀌었다.
        monkeypatch.setattr(module, "build_outcome_labels", _fake_build_outcome_labels)
        return calls

    def test_second_run_reuses_replenished_labels(self, tmp_path, monkeypatch) -> None:
        from disclosure_lit.eval.l2b_holdout import _build_labels

        calls = self._prepare(tmp_path, monkeypatch)

        cluster_ids1, labels1 = _build_labels(tmp_path / "raw", tmp_path)
        assert len(calls) == 1  # 첫 실행에서는 계산한다
        assert labels1 and cluster_ids1 == ["pc-0000"]

        cluster_ids2, labels2 = _build_labels(tmp_path / "raw", tmp_path)

        assert len(calls) == 1, "두 번째 실행에서 라벨을 다시 계산했다 — 캐시 교체가 안 된 것"
        assert labels2 == labels1
        assert cluster_ids2 == cluster_ids1


class TestRunConsoleEncoding:
    def test_run_survives_cp949_strict_stdout(self, tmp_path, monkeypatch, capsys) -> None:
        """Windows(cp949/strict) 콘솔에서 진행 안내의 em dash(U+2014)로 죽지 않아야 한다
        — L2a 러너(`run_eval.run()`)는 첫 줄에서 인코딩을 맞추는데 L2b 진입점에는 그
        처리가 빠져 있었다(PR #112 리뷰 @9abin)."""
        import io
        import sys

        import disclosure_lit.eval.l2b_holdout as module

        labels = [OutcomeLabel("c1", f"C{i}", "유상증자") for i in range(5)]
        monkeypatch.setattr(module, "_build_labels", lambda *a, **k: (["c1"], labels))
        # 유의 조합 0개 경로 → em dash가 들어간 안내 문구를 stdout으로 찍는다
        monkeypatch.setattr(module, "validate_significant_clusters", lambda *a, **k: [])

        with capsys.disabled():
            original = sys.stdout
            sys.stdout = io.TextIOWrapper(io.BytesIO(), encoding="cp949", errors="strict")
            try:
                module.run(tmp_path / "raw", tmp_path)  # UnicodeEncodeError 없이 끝나야 한다
            finally:
                sys.stdout = original
