"""계층 의존 방향 회귀 테스트 — 아래 계층이 위 계층을 import하지 못하게 막는다.

**왜 필요한가**(2026-09-24 구조 감사): 이전에 아래 두 건이 실제로 들어와 있었다.

- `eval/l2b_holdout.py`가 `demo/app.py`의 비공개 함수(`_build_outcome_labels`)를 import
  — 평가(L5)가 화면(L4)에 의존.
- `dictionary/recurrence.py`가 `eval/l2b_holdout.py`를 import — 도메인(L2b)이 평가(L5)에 의존.

둘이 합쳐지면 `dictionary → eval → demo → dictionary` 순환이 된다. 당시 앱이 부팅된
유일한 이유는 `demo` import가 파일 상단이 아니라 **함수 안에 있어서**였다 — 누군가 그
줄을 위로 올리는 순간 깨지는 상태였다. 그래서 이 테스트는 함수 안 import(지연 import)도
똑같이 잡는다.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parent.parent / "disclosure_lit"

# (이 패키지의 파일은, 이 모듈들을 import하면 안 된다) — 계층 역방향 금지 규칙.
#
# 방향은 CLAUDE.md의 레이어 표를 따른다: L0 dart → L1 retrieval → L2a factcheck /
# L2b dictionary → L3 report → L4 demo, 그리고 L5 eval은 이들을 가져다 쓰는 횡단 소비자다.
# **아래 계층이 위 계층을 import하는 것만** 금지한다 — 위에서 아래로는 정상이다.
# `demo`(L4)와 `eval`(L5)은 소비자라서 금지 규칙이 없다(아래 커버리지 테스트 참고).
_FORBIDDEN: dict[str, tuple[str, ...]] = {
    # L2b 도메인은 평가(L5)·화면(L4)을 몰라야 한다.
    "dictionary": ("eval", "demo", "report"),
    "factcheck": ("eval", "demo", "report"),
    # L1 검색은 L2b 도메인(dictionary)도 몰라야 한다 — 지금은 위반이 남아 있어
    # 아래 `_KNOWN_DEBT`에 적어뒀다.
    "retrieval": ("eval", "demo", "report", "dictionary"),
    "report": ("eval", "demo"),
    "dart": ("eval", "demo", "report", "factcheck", "dictionary", "retrieval"),
    # L5 평가는 아래 계층을 다 써도 되지만 **화면(L4)만은 안 된다** — 화면에서만 쓰는
    # 계산을 평가가 가져다 쓰면, 그 계산은 UI에 갇힌 채 두 소비자를 동시에 섬기게 된다.
    "eval": ("demo",),
    # 패키지가 아닌 최상위 모듈도 검사한다(2026-09-24 자체 리뷰 지적 — 디렉터리만 훑던
    # 첫 판은 `pipeline.py`에 `from disclosure_lit.demo import ...`를 넣어도 못 잡았다).
    "pipeline": ("eval", "demo"),
    "cache_store": ("eval", "demo", "report"),
    "embed": ("eval", "demo", "report", "factcheck", "dictionary", "retrieval"),
    "llm": ("eval", "demo", "report", "factcheck", "dictionary", "retrieval"),
    "config": ("eval", "demo", "report", "factcheck", "dictionary", "retrieval", "dart"),
}

# **아직 못 고친 역방향** — 고의로 남겨둔 부채이지 규칙의 예외가 아니다.
#
# `dictionary/cluster.py`가 `retrieval.phrase_index.PhraseCluster`를 최상단에서 가져오고,
# `retrieval/phrase_index.py`는 `dictionary.cluster`를 **함수 안에서** 되받는다 — 이번에
# 고친 것(eval→demo)과 똑같이 "지연 import가 순환을 가리는" 상태다. 푸는 데 필요한 건
# `PhraseCluster`를 두 모듈이 함께 볼 수 있는 자리로 옮기는 별도 작업이라, 발표(2026-09-29)
# 전에는 손대지 않기로 하고 여기 적어 눈에 보이게 둔다.
#
# 이 목록은 **늘어나면 안 되고**, 고치면 해당 줄을 지워야 테스트가 통과한다(아래 두 테스트).
_KNOWN_DEBT: dict[str, str] = {
    "disclosure_lit/retrieval/phrase_index.py -> disclosure_lit.dictionary": (
        "cluster_phrases_detailed·CLUSTER_MIN_SCORE를 함수 안에서 지연 import "
        "(dictionary/cluster.py가 PhraseCluster를 최상단에서 가져오므로 순환)"
    ),
}


def _imported_modules(path: Path) -> set[str]:
    """파일이 import하는 `disclosure_lit.<X>`의 X를 모은다(함수 안 지연 import 포함)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            parts = node.module.split(".")
            if parts[0] == "disclosure_lit":
                if len(parts) > 1:
                    found.add(parts[1])
                else:  # from disclosure_lit import cache_store
                    found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if parts[0] == "disclosure_lit" and len(parts) > 1:
                    found.add(parts[1])
    return found


def _source_files(name: str) -> list[Path]:
    """패키지면 그 안의 모든 `.py`, 최상위 모듈이면 그 파일 하나."""
    package = _SRC / name
    if package.is_dir():
        return sorted(package.rglob("*.py"))
    return [_SRC / f"{name}.py"]


def _violations(name: str) -> set[str]:
    forbidden = set(_FORBIDDEN[name])
    found: set[str] = set()
    for path in _source_files(name):
        for imported in sorted(_imported_modules(path) & forbidden):
            found.add(f"{path.relative_to(_SRC.parent).as_posix()} -> disclosure_lit.{imported}")
    return found


@pytest.mark.parametrize("name", sorted(_FORBIDDEN))
def test_layer_does_not_import_upper_layers(name: str) -> None:
    unexpected = sorted(_violations(name) - set(_KNOWN_DEBT))
    assert not unexpected, "계층 역방향 import:\n" + "\n".join(unexpected)


def test_known_debt_is_still_real() -> None:
    """부채 목록이 현실과 어긋나지 않게 한다.

    고쳤는데 목록에 남아 있으면 "아직 문제가 있다"는 잘못된 인상을 주고, 다음 사람이
    이미 끝난 일을 다시 들여다보게 된다. 고쳤으면 이 목록에서 지워라.
    """
    actual = set().union(*(_violations(name) for name in _FORBIDDEN))
    stale = sorted(set(_KNOWN_DEBT) - actual)
    assert not stale, "이미 고쳐졌으니 _KNOWN_DEBT에서 지우세요:\n" + "\n".join(stale)


def test_rule_table_covers_every_source_module() -> None:
    """새 패키지·모듈이 생기면 이 표에도 규칙을 정하고 넘어가라는 장치다."""
    names = {p.name for p in _SRC.iterdir() if p.is_dir() and not p.name.startswith("__")}
    names |= {p.stem for p in _SRC.glob("*.py") if not p.name.startswith("__")}
    # demo(L4)만 금지 규칙이 없다 — 가장 위 계층이라 아래를 모두 가져다 쓴다.
    assert names - set(_FORBIDDEN) == {"demo"}
