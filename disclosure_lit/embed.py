"""임베딩 — narrative_qa/embed.py 패턴 재사용. L1 검색 + L2b 화법 클러스터링(B)이 공용.

팀 결정 완료(PR #27, 2026-09-11): `EMBED_BACKEND=local` + `dragonkue/BGE-m3-ko` +
1024차원. `openai` 백엔드로 스왑하는 경로도 narrative_qa 패턴을 따라 남겨뒀지만,
disclosure_lit 에는 아직 `openai_api_key` 설정이 없어서(공통-1, LLM 계층 이식 대기 중)
실제로 쓰려면 그것부터 연결해야 한다 — 지금은 `local` 만 검증됐다.

반환은 항상 float32 numpy (N, dim), L2 정규화됨(코사인 유사도 = 내적).
"""

from __future__ import annotations

import sys

import numpy as np

from disclosure_lit.config import settings

_local_model = None
_openai_client = None


def _stderr_flush_is_safe() -> bool:
    """Streamlit(특히 이 레포의 Windows 헤드리스 실행) 프로세스 안에서는 `sys.stderr`가
    tqdm이 기대하는 스트림이 아니라서 `.flush()`가 `OSError: [Errno 22] Invalid argument`를
    던진다(실측 2026-09-18 — `_cluster_meanings()` 호출 시 `match_many()` -> `embed_texts()`가
    후보 문구 256개 초과로 진행바를 켜자마자 요청 전체가 죽었고, 그게 "13분 동안 멈춘
    것처럼 보이는" 현상의 실제 원인이었다). tqdm에게 진행바를 맡기기 전에 먼저 확인해서,
    이 환경에서만 조용히 끈다 — CLI 배치 스크립트 등 정상 환경의 진행바는 그대로 유지."""
    try:
        sys.stderr.flush()
    except OSError:
        return False
    return True


# 실측(L5 run_eval.py 첫 실행, CompanyDisclosureIndex.index() 경유) — dragonkue/BGE-m3-ko는
# 최대 8192 토큰까지 자르지 않고 그대로 받는 long-context 모델이라, "본문(미분할)" 폴백
# 섹션처럼 25만자를 넘는 텍스트가 배치에 섞이면 그 섹션 하나의 시퀀스 길이만으로 어텐션
# 메모리가 8GB GPU 기준 16GB 할당 시도로 터진다(dictionary/candidates.py::MAX_PHRASE_LENGTH
# 가 잡은 것과 같은 근본 원인 — 거기는 화법 후보 "문장" 단위라 500자로 좁게 잡았지만, 여기는
# L1 검색용 섹션 전체 임베딩이 공용 진입점이라 호출부마다 따로 막는 대신 여기서 한 번에
# 막는다). 검색 관련도는 텍스트 앞부분 몇 문단으로도 충분히 잡히므로, 임베딩용 입력만
# 자르고(저장되는 DisclosureSection.text 자체는 그대로 둔다) 의미 손실은 최소화한다.
#
# 2차 실측(L5 run_eval.py, 114개사 전체 인덱싱) — 최초 2000자 캡으로는 단일 텍스트발
# OOM은 막았지만, batch_size=64 그대로면 "2000자에 가까운 텍스트 64개짜리 배치"가
# 여전히 8GB GPU 메모리를 거의 다 채워(7.9GB/8GB, GPU-util 100%인데 전력은 26W로 낮음 —
# 스와핑/스래싱 패턴) 배치당 150~190초까지 느려졌다(260배치 기준 완주까지 10시간+
# 추정). 글자 수 캡과 배치 크기 둘 다 낮춰서 여유를 확보한다.
MAX_EMBED_CHARS = 1000
LOCAL_BATCH_SIZE = 16


def _truncate(texts: list[str]) -> list[str]:
    return [t[:MAX_EMBED_CHARS] for t in texts]


def _local():
    global _local_model
    if _local_model is None:
        from sentence_transformers import SentenceTransformer

        _local_model = SentenceTransformer(settings.local_embed_model)
    return _local_model


def _openai():
    global _openai_client
    if _openai_client is None:
        import os

        from openai import OpenAI

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "EMBED_BACKEND=openai 인데 OPENAI_API_KEY 가 없습니다. "
                "팀 결정은 local(dragonkue/BGE-m3-ko)이라 기본값 그대로 두는 걸 권장합니다."
            )
        _openai_client = OpenAI(api_key=api_key)
    return _openai_client


def embed_texts(texts: list[str]) -> np.ndarray:
    """여러 문서를 임베딩한다. (len(texts), dim) float32, L2 정규화됨."""
    if not texts:
        return np.zeros((0, settings.embed_dim), dtype=np.float32)

    texts = _truncate(texts)

    if settings.embed_backend == "local":
        vecs = _local().encode(
            texts,
            batch_size=LOCAL_BATCH_SIZE,
            normalize_embeddings=True,
            show_progress_bar=len(texts) > 256 and _stderr_flush_is_safe(),
        )
        return np.asarray(vecs, dtype=np.float32)

    client = _openai()
    batch = 128
    out: list[list[float]] = []
    for i in range(0, len(texts), batch):
        chunk = [t.replace("\n", " ") for t in texts[i : i + batch]]
        resp = client.embeddings.create(model=settings.embed_model, input=chunk)
        out.extend(d.embedding for d in resp.data)
    arr = np.asarray(out, dtype=np.float32)
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return arr / norms


def embed_query(text: str) -> np.ndarray:
    """단일 쿼리 -> (dim,) float32 (정규화됨)."""
    return embed_texts([text])[0]
