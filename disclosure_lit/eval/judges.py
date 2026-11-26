"""LLM judge — gold set 기대 사실(expected_facts) 대비 팩트체크 결과 판정 — L5.

narrative_qa/eval/judges.py::judge_accuracy의 "기대 포인트 목록 vs 후보" 구조를 그대로
가져왔다 — "기대 사실 vs 팩트체크가 실제로 낸 사실"로 도메인만 바꿨다. 두 방향을
따로 판정한다(narrative_qa는 정확도 하나만 봤지만, 여기는 REQ-F-008이 "정밀도·재현율"
둘 다 요구한다):

- 재현율(recall): 기대 사실 중 몇 개를 실제로 찾아냈는가
- 정밀도 관점 — hallucination: 실제로 낸 사실 중 몇 개가 근거 없이 지어낸 것인가
  (docs/06_open_questions.md 최고 심각도 리스크 — groundedness 없이 사실을 만들어내면
  규제 위반과 직결)
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from disclosure_lit.llm import complete

_JUDGE_TIER = "mini"

_RECALL_SYS = """공시 팩트체크 결과를 채점한다.
"기대 사실" 목록과 "팩트체크가 실제로 낸 사실" 목록이 주어지면, 기대 사실 각각이
팩트체크 결과 중 하나로 실질적으로 커버됐는지(같은 의미를 담고 있는지) 판단하라.
표현이 달라도 같은 내용이면 커버된 것으로 본다."""

_HALLUCINATION_SYS = """공시 팩트체크가 낸 사실 목록과, 근거가 되는 "기대 사실"
목록(정답)이 주어진다. 팩트체크 결과 각각이 기대 사실 어디에도 없는 내용을 지어낸
것인지(hallucination) 판단하라. 기대 사실에 없어도 사소한 재서술·요약이면 지어낸 게
아니다 — 명백히 근거 없는 새로운 주장일 때만 지어낸 것으로 본다."""


def _pad_bools(values: list[bool], target_len: int) -> list[bool]:
    """모델 응답 길이를 강제로 맞춘다(narrative_qa/eval/judges.py와 동일 원칙) — 모델이
    개수를 안 맞추면 패딩/절단해서 최소한 집계가 죽지는 않게 한다. `judge_recall`/
    `judge_hallucination` 둘 다 같은 방어 로직을 쓰므로 여기로 뽑았다(PR #53 리뷰,
    @9abin 지적 — narrative_qa까지 포함하면 세 번째 중복이었음)."""
    return (values + [False] * target_len)[:target_len]


class RecallVerdict(BaseModel):
    covered: list[bool] = Field(default_factory=list)  # expected_facts와 같은 길이

    @property
    def recall(self) -> float:
        if not self.covered:
            return 1.0
        return sum(self.covered) / len(self.covered)


class HallucinationVerdict(BaseModel):
    fabricated: list[bool] = Field(default_factory=list)  # actual_facts와 같은 길이

    @property
    def hallucination_rate(self) -> float:
        if not self.fabricated:
            return 0.0
        return sum(self.fabricated) / len(self.fabricated)


def judge_recall(expected_facts: list[str], actual_facts: list[str]) -> RecallVerdict:
    """기대 사실 중 몇 개가 실제 팩트체크 결과로 커버됐는지 판정한다.

    기대 사실이 없으면(gold 데이터 결함) recall=1.0으로 취급한다 — 채점 불능을
    실패로 왜곡하지 않는다.
    """
    if not expected_facts:
        return RecallVerdict(covered=[])
    user = (
        "기대 사실:\n"
        + "\n".join(f"{i + 1}. {f}" for i, f in enumerate(expected_facts))
        + "\n\n팩트체크 결과:\n"
        + ("\n".join(f"- {f}" for f in actual_facts) if actual_facts else "(없음)")
    )
    result = complete(_RECALL_SYS, user, tier=_JUDGE_TIER, schema=RecallVerdict)
    verdict = result.parsed or RecallVerdict(covered=[False] * len(expected_facts))
    if len(verdict.covered) != len(expected_facts):
        padded = _pad_bools(verdict.covered, len(expected_facts))
        verdict = verdict.model_copy(update={"covered": padded})
    return verdict


def judge_hallucination(expected_facts: list[str], actual_facts: list[str]) -> HallucinationVerdict:
    """실제로 낸 사실 중 근거 없이 지어낸 게 있는지 판정한다.

    낸 사실이 없으면(예: 관련 공시를 못 찾음) hallucination_rate=0.0 — 아무것도 안
    지어낸 게 맞다.
    """
    if not actual_facts:
        return HallucinationVerdict(fabricated=[])
    user = (
        "기대 사실(정답):\n"
        + "\n".join(f"- {f}" for f in expected_facts)
        + "\n\n팩트체크가 낸 사실:\n"
        + "\n".join(f"{i + 1}. {f}" for i, f in enumerate(actual_facts))
    )
    result = complete(_HALLUCINATION_SYS, user, tier=_JUDGE_TIER, schema=HallucinationVerdict)
    verdict = result.parsed or HallucinationVerdict(fabricated=[False] * len(actual_facts))
    if len(verdict.fabricated) != len(actual_facts):
        padded = _pad_bools(verdict.fabricated, len(actual_facts))
        verdict = verdict.model_copy(update={"fabricated": padded})
    return verdict
