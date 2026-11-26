"""disclosure_lit.embed 단위 테스트. 로컬 모델 로딩이 필요한 것만 `slow` 마커로 분리."""

from __future__ import annotations

import dataclasses

import numpy as np
import pytest

import disclosure_lit.embed as embed_module
from disclosure_lit.config import settings


def _with_backend(backend: str):
    """settings 는 frozen dataclass 라 monkeypatch.setattr 로 필드를 못 바꾼다 —
    dataclasses.replace 로 새 인스턴스를 만들어 embed_module.settings 이름 자체를
    바꿔치기한다."""
    return dataclasses.replace(settings, embed_backend=backend)


class TestEmbedTextsEmptyInput:
    def test_returns_correctly_shaped_empty_array(self) -> None:
        result = embed_module.embed_texts([])
        assert result.shape == (0, settings.embed_dim)
        assert result.dtype == np.float32


class TestLocalBackendDispatch:
    def test_calls_local_model_encode_with_normalization(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = {}

        class FakeModel:
            def encode(self, texts, *, batch_size, normalize_embeddings, show_progress_bar):
                calls["texts"] = texts
                calls["normalize_embeddings"] = normalize_embeddings
                return np.ones((len(texts), 4), dtype=np.float32)

        monkeypatch.setattr(embed_module, "settings", _with_backend("local"))
        monkeypatch.setattr(embed_module, "_local", lambda: FakeModel())

        result = embed_module.embed_texts(["a", "b"])

        assert calls["texts"] == ["a", "b"]
        assert calls["normalize_embeddings"] is True
        assert result.shape == (2, 4)
        assert result.dtype == np.float32

    def test_embed_query_returns_single_vector(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class FakeModel:
            def encode(self, texts, **kwargs):
                return np.full((len(texts), 4), 0.5, dtype=np.float32)

        monkeypatch.setattr(embed_module, "settings", _with_backend("local"))
        monkeypatch.setattr(embed_module, "_local", lambda: FakeModel())

        vec = embed_module.embed_query("테스트 문장")
        assert vec.shape == (4,)


class TestMaxEmbedCharsTruncation:
    """실측(L5 run_eval.py 첫 실행) — CompanyDisclosureIndex.index()가 "본문(미분할)"
    폴백 섹션(25만자+)을 그대로 embed_texts에 넘겨 CUDA OOM(16GB 할당 시도, 8GB GPU)이
    났다. 공용 진입점인 embed_texts()에서 길게 자르는지 확인한다."""

    def test_local_backend_truncates_long_text_before_encode(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = {}

        class FakeModel:
            def encode(self, texts, **kwargs):
                calls["texts"] = texts
                return np.ones((len(texts), 4), dtype=np.float32)

        monkeypatch.setattr(embed_module, "settings", _with_backend("local"))
        monkeypatch.setattr(embed_module, "_local", lambda: FakeModel())

        huge = "가" * (embed_module.MAX_EMBED_CHARS + 1000)
        embed_module.embed_texts([huge, "짧은 문장"])

        assert len(calls["texts"][0]) == embed_module.MAX_EMBED_CHARS
        assert calls["texts"][1] == "짧은 문장"  # 짧은 텍스트는 그대로

    def test_openai_backend_truncates_long_text_before_encode(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = {}

        class FakeResponse:
            def __init__(self, n: int) -> None:
                self.data = [type("D", (), {"embedding": [0.5, 0.5]})() for _ in range(n)]

        class FakeEmbeddings:
            def create(self, *, model, input):
                calls["input"] = input
                return FakeResponse(len(input))

        class FakeClient:
            embeddings = FakeEmbeddings()

        monkeypatch.setattr(embed_module, "settings", _with_backend("openai"))
        monkeypatch.setattr(embed_module, "_openai", lambda: FakeClient())

        huge = "나" * (embed_module.MAX_EMBED_CHARS + 1000)
        embed_module.embed_texts([huge])

        assert len(calls["input"][0]) == embed_module.MAX_EMBED_CHARS


class TestOpenAiBackendDispatch:
    def test_raises_clear_error_without_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(embed_module, "settings", _with_backend("openai"))
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setattr(embed_module, "_openai_client", None)  # 캐시된 클라이언트 초기화

        with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
            embed_module.embed_texts(["안녕"])


@pytest.mark.slow
class TestRealLocalModel:
    """실제 dragonkue/BGE-m3-ko 를 로드해서 검증한다 — 첫 실행 시 모델 다운로드 발생."""

    def test_real_embedding_shape_and_normalization(self) -> None:
        vecs = embed_module.embed_texts(
            ["유상증자 결정 관련 공시입니다.", "자기주식 취득 신탁계약 해지"]
        )

        assert vecs.shape == (2, settings.embed_dim)
        norms = np.linalg.norm(vecs, axis=1)
        np.testing.assert_allclose(norms, 1.0, atol=1e-3)  # L2 정규화 확인

    def test_similar_sentences_have_higher_cosine_similarity(self) -> None:
        v1 = embed_module.embed_query("유상증자를 결정하였습니다.")
        v2 = embed_module.embed_query("이사회는 유상증자를 의결했습니다.")
        v3 = embed_module.embed_query("오늘 날씨가 맑습니다.")

        sim_related = float(np.dot(v1, v2))
        sim_unrelated = float(np.dot(v1, v3))
        assert sim_related > sim_unrelated
