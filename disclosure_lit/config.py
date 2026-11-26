"""설정 로딩 — narrative_qa/config.py와 동일한 fail-fast 패턴을 재사용한다."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(PROJECT_ROOT / ".env")


def _env(key: str, default: str | None = None) -> str:
    value = os.getenv(key, default)
    if value is None:
        raise RuntimeError(f"env var {key} is missing. Check .env.")
    return value


@dataclass(frozen=True)
class Settings:
    # --- DART ---
    dart_api_key: str

    # --- LLM (claim 추출 / claim verification에 사용) ---
    # 키는 여기서 강제하지 않는다 — narrative_qa/config.py와 같은 이유로, 실제
    # LLM 클라이언트를 생성하는 시점(llm.py)에 명확히 실패하게 한다(DartClient의
    # dart_api_key fail-fast와 같은 패턴).
    llm_provider: str  # openai | anthropic | gemini
    openai_api_key: str
    anthropic_api_key: str
    gemini_api_key: str
    model_mini: str
    model_final: str

    # --- 임베딩 (L1 검색 + 화법 클러스터링) ---
    # 팀 결정 완료(PR #27, 2026-09-11): local + dragonkue/BGE-m3-ko + 1024차원.
    embed_backend: str  # local | openai
    local_embed_model: str
    embed_dim: int
    embed_model: str  # embed_backend=openai 로 스왑할 때만 사용
    # 골드셋 검증 전에는 비활성화. 검색 점수는 확률이 아니며 질의/모델에 종속된다.
    factcheck_min_relevance_score: float | None = None


def load_settings() -> Settings:
    return Settings(
        dart_api_key=_env("DART_API_KEY", ""),
        llm_provider=_env("LLM_PROVIDER", "openai"),
        openai_api_key=_env("OPENAI_API_KEY", ""),
        anthropic_api_key=_env("ANTHROPIC_API_KEY", ""),
        gemini_api_key=_env("GEMINI_API_KEY", ""),
        model_mini=_env("MODEL_MINI", ""),
        model_final=_env("MODEL_FINAL", ""),
        embed_backend=_env("EMBED_BACKEND", "local"),
        local_embed_model=_env("LOCAL_EMBED_MODEL", "dragonkue/BGE-m3-ko"),
        embed_dim=int(_env("EMBED_DIM", "1024")),
        embed_model=_env("EMBED_MODEL", "text-embedding-3-small"),
        factcheck_min_relevance_score=(
            float(_env("FACTCHECK_MIN_RELEVANCE_SCORE"))
            if _env("FACTCHECK_MIN_RELEVANCE_SCORE", "").strip()
            else None
        ),
    )


settings = load_settings()
