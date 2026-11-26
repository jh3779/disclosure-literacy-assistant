from __future__ import annotations

import os

import pytest

# 이슈 #102 — `faiss`와 `torch`(로컬 임베딩용 sentence-transformers, `local` extra)가 같은
# pytest 프로세스에 함께 로드되면 OpenMP 런타임이 중복 초기화돼 크래시한다
# ("OMP: Error #15"). CI는 `local` extra를 설치하지 않아 이 조합이 없어 드러나지 않는다
# (`.github/workflows/ci.yml` 참고). 수집 시점에 두 라이브러리가 같은 프로세스에 섞이므로,
# 어느 한 테스트 파일이 아니라 여기(모든 테스트보다 먼저 로드되는 conftest.py 최상단)에서
# 임시 우회를 건다. 주의: LLVM 공식 문서가 "지원하지 않는 우회"로 명시하는 플래그다 —
# 중복 런타임 문제 자체를 없애지 않고, 비정상 종료나 잘못된 결과가 날 수 있다고 적혀 있다.
# 여기서는 테스트 프로세스에만 한정해 크래시를 없애는 용도로만 쓴다(이미 설정된 값이 있으면
# 사용자가 직접 지정한 것이므로 덮어쓰지 않는다). 근본 조치는 호환되는 단일 OpenMP 런타임을
# 쓰도록 맞추거나, 충돌하는 모델 테스트를 별도 프로세스로 격리하는 것.
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")


@pytest.fixture(autouse=True)
def _skip_llm_without_key(request):
    if request.node.get_closest_marker("llm") and not os.getenv("OPENAI_API_KEY", "").startswith(
        "sk-"
    ):
        pytest.skip("OPENAI_API_KEY 미설정")


def _dart_api_key_looks_valid() -> bool:
    key = os.getenv("DART_API_KEY", "").strip()
    return bool(key) and key not in {"...", "your-key-here"}


@pytest.fixture(autouse=True)
def _skip_dart_integration_without_key(request):
    if request.node.get_closest_marker("dart_integration") and not _dart_api_key_looks_valid():
        pytest.skip("DART_API_KEY 미설정 — opendart.fss.or.kr 에서 발급 후 .env 에 채울 것")
