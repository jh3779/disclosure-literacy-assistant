"""Provider-swappable LLM 호출 지점 — narrative_qa/llm.py 그대로 재사용(CLAUDE.md 스택 절).

모든 LLM 호출은 `complete()` 를 거친다. `LLM_PROVIDER` 로 openai / anthropic / gemini 전환.
factcheck/claim.py::extract_claim, factcheck/verify.py::verify_claim(groundedness guard
포함)이 이 모듈을 통해 LLM을 부른다.

- tier="mini"  : claim 추출 / groundedness guard 등 저비용 호출 -> MODEL_MINI
- tier="final" : claim verification 최종 결과 생성            -> MODEL_FINAL
- schema=<Pydantic BaseModel> : 구조화 출력 (OpenAI .parse / Anthropic forced-tool JSON /
  Gemini response_schema)

narrative_qa/llm.py에서 옮겨온 것 — 바뀐 건 `from disclosure_lit.config import settings`
한 줄뿐이다(패턴 출처: narrative_qa/llm.py 자체 docstring 참고).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import TypeVar

from pydantic import BaseModel

from disclosure_lit.config import settings

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# 2026-09-22(상세 감사, "다른 시각" 재검토) — 세 프로바이더 클라이언트 전부 SDK
# 기본 타임아웃(OpenAI/Anthropic 실측: connect=5s, read/write/pool=600s)에
# 의존하고 있었다. `factcheck/verify.py::verify_claim()`이 이 호출을 `demo/
# app.py`의 `st.spinner()` 안에서 동기로 하는데, 그 안내 문구("처음 조회하는
# 종목은 분석에 몇 분 정도 걸릴 수 있어요")가 "느린 건 정상"이라는 기대를 심어줘,
# LLM API가 느려지거나(레이트리밋 backpressure 등) 사실상 멈추면 사용자는 정상
# 처리와 진짜 행(hang)을 구분할 방법이 없고 취소 수단도 없다 — `embed.py::_stderr_
# flush_is_safe()`가 막았던 "조용한 멈춤" 사고와 같은 유형. `dart/client.py`는
# 이미 명시적 타임아웃(10s/30s)이 있어 이 패턴이 빠진 곳은 LLM 클라이언트뿐이었다.
# 90초는 구조화 출력(스키마 채우기)까지 포함한 정상 완료 시간에 여유를 두면서도,
# SDK 기본값(최대 10분)보다는 훨씬 짧게 잡은 값이다 — 실제로 이보다 오래 걸리면
# "느린 정상 처리"가 아니라 장애로 보고 명확히 실패시키는 게 더 안전하다는 판단.
LLM_REQUEST_TIMEOUT_SECONDS = 90.0


@dataclass
class LLMResult:
    text: str
    parsed: BaseModel | None
    input_tokens: int
    output_tokens: int

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


def _model(tier: str) -> str:
    return settings.model_final if tier == "final" else settings.model_mini


# --------------------------------------------------------------------------- OpenAI

_openai_client = None


def _openai():
    global _openai_client
    if _openai_client is None:
        from openai import OpenAI

        _openai_client = OpenAI(
            api_key=settings.openai_api_key, timeout=LLM_REQUEST_TIMEOUT_SECONDS
        )
    return _openai_client


def _complete_openai(
    system: str, user: str, *, tier: str, schema: type[T] | None, temperature: float
) -> LLMResult:
    client = _openai()
    model = _model(tier)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    if schema is not None:
        resp = client.beta.chat.completions.parse(
            model=model, messages=messages, temperature=temperature, response_format=schema
        )
        msg = resp.choices[0].message
        usage = resp.usage
        return LLMResult(
            text=msg.content or "",
            parsed=msg.parsed,
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
        )
    resp = client.chat.completions.create(model=model, messages=messages, temperature=temperature)
    usage = resp.usage
    return LLMResult(
        text=resp.choices[0].message.content or "",
        parsed=None,
        input_tokens=usage.prompt_tokens if usage else 0,
        output_tokens=usage.completion_tokens if usage else 0,
    )


# --------------------------------------------------------------------------- Anthropic

_anthropic_client = None


def _anthropic():
    global _anthropic_client
    if _anthropic_client is None:
        from anthropic import Anthropic

        _anthropic_client = Anthropic(
            api_key=settings.anthropic_api_key, timeout=LLM_REQUEST_TIMEOUT_SECONDS
        )
    return _anthropic_client


def _complete_anthropic(
    system: str, user: str, *, tier: str, schema: type[T] | None, temperature: float
) -> LLMResult:
    client = _anthropic()
    model = _model(tier)

    if schema is not None:
        tool = {
            "name": "emit",
            "description": "결과를 이 스키마로 반환",
            "input_schema": schema.model_json_schema(),
        }
        resp = client.messages.create(
            model=model,
            max_tokens=4096,
            temperature=temperature,
            system=system,
            messages=[{"role": "user", "content": user}],
            tools=[tool],
            tool_choice={"type": "tool", "name": "emit"},
        )
        payload = next(
            (b.input for b in resp.content if getattr(b, "type", None) == "tool_use"), {}
        )
        return LLMResult(
            text=json.dumps(payload, ensure_ascii=False),
            parsed=schema.model_validate(payload),
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
        )

    resp = client.messages.create(
        model=model,
        max_tokens=4096,
        temperature=temperature,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    return LLMResult(
        text=text,
        parsed=None,
        input_tokens=resp.usage.input_tokens,
        output_tokens=resp.usage.output_tokens,
    )


# --------------------------------------------------------------------------- Gemini

_gemini_client = None


def _gemini():
    global _gemini_client
    if _gemini_client is None:
        from google import genai
        from google.genai.types import HttpOptions

        if not settings.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY 가 필요합니다 (.env). aistudio.google.com/apikey")
        _gemini_client = genai.Client(
            api_key=settings.gemini_api_key,
            # HttpOptions.timeout 단위는 밀리초(SDK 문서 기준) — 위 LLM_REQUEST_
            # TIMEOUT_SECONDS와 맞춘다.
            http_options=HttpOptions(timeout=int(LLM_REQUEST_TIMEOUT_SECONDS * 1000)),
        )
    return _gemini_client


def _complete_gemini(
    system: str, user: str, *, tier: str, schema: type[T] | None, temperature: float
) -> LLMResult:
    from google.genai import types

    client = _gemini()
    model = _model(tier)
    cfg: dict = {"system_instruction": system, "temperature": temperature}
    if schema is not None:
        cfg["response_mime_type"] = "application/json"
        cfg["response_schema"] = schema

    resp = client.models.generate_content(
        model=model, contents=user, config=types.GenerateContentConfig(**cfg)
    )
    um = getattr(resp, "usage_metadata", None)
    parsed = None
    text = resp.text or ""
    if schema is not None:
        parsed = getattr(resp, "parsed", None)
        if parsed is None and text:  # SDK가 못 파싱하면 수동
            try:
                parsed = schema.model_validate_json(text)
            except Exception:
                # 2026-09-22(상세 감사) — 예전엔 이 실패가 로그 없이 parsed=None으로만
                # 내려갔다. 호출부(예: factcheck/verify.py)가 파싱 실패를 "관련 공시
                # 없음"과 구분 못 하게 만드는 원인 중 하나라 최소한 여기서는 남긴다.
                logger.warning(
                    "Gemini 구조화 출력 수동 파싱 실패(schema=%s) — 원본 응답: %.200r",
                    schema.__name__,
                    text,
                )
                parsed = None
    return LLMResult(
        text=text,
        parsed=parsed,
        input_tokens=getattr(um, "prompt_token_count", 0) or 0,
        output_tokens=getattr(um, "candidates_token_count", 0) or 0,
    )


# --------------------------------------------------------------------------- public


def complete(
    system: str,
    user: str,
    *,
    tier: str = "mini",
    schema: type[T] | None = None,
    temperature: float = 0.0,
) -> LLMResult:
    provider = settings.llm_provider
    if provider == "gemini":
        return _complete_gemini(system, user, tier=tier, schema=schema, temperature=temperature)
    if provider == "anthropic":
        return _complete_anthropic(system, user, tier=tier, schema=schema, temperature=temperature)
    return _complete_openai(system, user, tier=tier, schema=schema, temperature=temperature)
