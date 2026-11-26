"""팩트체크 gold set 스키마 — L5. 담당: C(우지훈) 수집, 전원 검수.

2026-09-14 DEC-010으로 담당 정정(.agent/DECISIONS.md — 이 문서·NAVIGATION.md는 처음부터
C 담당으로 일관됐으나 .agent/PROJECT_CONTEXT.md의 계획 메모만 B로 잘못 적혀 있었음).
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

DEFAULT_GOLD_PATH = Path(__file__).resolve().parent / "gold" / "rumor_cases.jsonl"


class RumorGoldCase(BaseModel):
    rumor_text: str
    corp_code: str
    expected_facts: list[str]
    source_url: str


def load_gold_cases(path: Path = DEFAULT_GOLD_PATH) -> list[RumorGoldCase]:
    """gold set jsonl을 읽는다.

    파일이 없으면 빈 리스트 — REQ-F-008(Should) 평가는 gold set이 갖춰진 뒤에나
    의미가 있으므로, 없다고 에러를 던지진 않는다(candidates.py::load_sections_jsonl과
    동일 원칙).

    각 사례는 실제 DART 공시(`source_url`)로 검증된 것만 담는다 — "풍문 또는 보도에
    대한 해명" 공시가 현재까지는 가장 확실한 gold 소스다(rumor_text=보도 내용,
    expected_facts=해명공시가 실제로 확인/부인한 사실, corp_code=해당 종목).
    """
    if not path.exists():
        return []
    cases: list[RumorGoldCase] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            cases.append(RumorGoldCase(**json.loads(line)))
    return cases
