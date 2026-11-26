"""클레임 vs 공시 사실 대조(claim verification) — L2a. 담당: B(김가빈)."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, replace
from typing import Literal

from pydantic import BaseModel

from disclosure_lit.dart.ingest import DisclosureSection
from disclosure_lit.factcheck.compliance import find_investment_advice
from disclosure_lit.llm import complete

logger = logging.getLogger(__name__)

Polarity = Literal["positive", "negative", "neutral"]

# 이슈 #99(실측: "삼성전자 mou 체결설" 라이브 테스트) — 로컬 코퍼스에 MOU 관련 공시가
# 아예 없는데도(직접 대조 확인), search()가 그나마 코사인 유사도가 제일 높은 무관한
# 문서 10건(이사회 운영 현황·다른 루머 해명·채무보증 등)을 채워 반환했고, 이 프롬프트가
# "같은 회사에 관한 내용"이라는 이유만으로 그중 일부를 "참고 사실"(neutral)로 뽑아냈다.
# "관련 공시 없음"이 정직한 정답인 상황에서 무관한 내용을 관련 있는 것처럼 보여준 것 —
# 아래 "관련성 판단" 문단과 _GUARD_SYSTEM_PROMPT의 클레임 관련성 재검사로 이중 방어한다.
_EXTRACT_SYSTEM_PROMPT = """당신은 루머(클레임)가 실제 공시 문서로 뒷받침되는지 사실만
나열하는 팩트체커입니다. 등급·점수·매수매도 의견·목표가는 절대 출력하지 않습니다.

아래 "공시 섹션 목록"에 실제로 쓰여 있는 내용에서만 사실을 뽑으세요 — 목록에 없는 내용을
지어내면 안 됩니다.

**관련성 판단(중요)**: 어떤 섹션이 클레임과 "같은 회사"에 관한 내용이라는 것만으로는
충분하지 않습니다. 그 섹션이 클레임이 주장하는 **구체적인 사안**을 직접 다루고 있어야
합니다. 예를 들어 클레임이 "OO전자, MOU 체결설"이면, 그 회사의 이사회 운영 현황이나
전혀 다른 계약(예: 채무보증, 위탁생산계약)에 관한 섹션은 "같은 회사"에 관한 것일 뿐 이
클레임과는 무관합니다 — 이런 섹션에서는 사실을 뽑지 마세요.

**목록에 클레임과 실질적으로 관련된 내용이 전혀 없으면, 억지로 사실을 만들어내지 말고
빈 목록을 반환하세요.** "관련 공시를 찾지 못했습니다"라는 정직한 결과가, 무관한 내용을
"참고 사실"로 포장해 보여주는 것보다 항상 낫습니다.

**무관함을 스스로 서술하지 말고, 그냥 빼세요.** 어떤 섹션이 클레임과 무관하다고
판단했다면, "이는 클레임과 직접적인 관련이 없는 별도의 사안입니다" 같은 문장을 fact로
적어서 items에 넣지 마세요 — 그 섹션은 애초에 items에 등장하지 않아야 합니다. fact
문장 안에 "무관하다/관련 없다/별도의/직접적인 관련이 없으나" 같은 표현이 들어간다면,
그건 이미 관련성 판단에 실패한 항목이라는 신호입니다 — 그런 설명을 쓰고 싶어지는
순간, 그 사실 후보 자체를 통째로 버리세요.

각 사실(fact)마다:
- fact: 공시에 실제로 쓰인 내용을 그대로 요약한 한 문장(추측·과장 금지). **표에서 뽑은
  사실은 숫자만 떼어오지 말고, 그 행에 같이 있는 맥락(관계·직위·항목명 등)을 반드시
  포함하세요.** DART 표는 구분자 없이 "성 명 관 계 주식의종류 소유주식수 ... (주)동오
  홀딩스 본인 보통주 5,981,170 30.64 ... 이병만 계열사임원 보통주 3,032,463 15.53 ...
  이용진 계열사임원 보통주 3,377,931 17.30 ..." 처럼 헤더 한 번 + 행들이 공백으로만
  죽 이어진 형태로 옵니다 — "관계" 열의 값(본인/계열사임원/친인척 등)이 반복돼도
  건너뛰지 말고, **행마다 그 사람 이름 바로 다음에 오는 관계 값을 하나하나 그대로
  붙여서** 적으세요: "이병만(계열사임원)의 지분율은 15.53%이다", "이용진(계열사임원)의
  지분율은 17.30%이다"처럼 — 앞사람과 관계가 같아 보여도 매 사람마다 반드시 명시하고,
  절대 첫 번째 행에만 관계를 적고 나머지는 생략하지 마세요.
- section_index: 그 사실의 근거가 된 섹션 번호(목록의 [번호]). 특정 섹션 하나로 못 좁히면
  가장 핵심적인 섹션 번호 하나를 고른다.
- polarity: 이 사실이 클레임을 "positive"(뒷받침) / "negative"(반박·배치) / "neutral"
  (클레임의 구체적 사안과 직접 관련은 있으나 뒷받침도 반박도 아님) 중 무엇으로 만드는지
  — "neutral"도 클레임의 사안과 실질적으로 관련 있어야 한다. 단순히 같은 회사에 관한
  일반적인 정보라는 이유만으로는 "neutral"조차 아니다.
  **클레임이 변화("매도", "매수", "증가", "감소", "확대", "축소" 등)를 주장하면, 그
  사실 문장 하나만 보고 판단하지 말고 목록 안의 다른 시점 수치와 비교하세요.** 예를
  들어 클레임이 "OO 대표이사 지분 대량 매도설"인데, 목록에 그 사람의 지분율이
  여러 시점(예: 2025-09: 67.53%, 2026-01: 67.78%)으로 나와 있다면, 각 수치를 따로
  neutral로 두지 말고 **가장 이른 시점과 가장 최근 시점을 비교**해서 판단하세요 —
  줄었으면 "positive"(매도설을 뒷받침), 늘었거나 그대로면 "negative"(매도설을
  반박) 입니다. 비교 결과를 fact 문장에 그대로 반영하세요(예: "이용진(대표이사)의
  지분율은 2025년 9월 67.53%에서 2026년 1월 67.78%로 오히려 늘어, 매도설과 배치된다").
  비교할 다른 시점이 없으면(단일 시점 수치만 있으면) 그 수치 하나만으로는 변화 여부를
  알 수 없으니 neutral로 둔다.
  **클레임이 특정 인물/법인을 지목하면, 반드시 그 사람 본인의 수치로만 판단하세요.**
  예를 들어 클레임이 "이용진 대표이사 지분 대량 매도설"인데 표에 여러 사람(이용진,
  이병만, 이승연 등)의 지분 변동이 같이 나와 있다면, **이병만이나 이승연의 지분이
  줄었다는 사실은 이용진의 매도설을 뒷받침하지 않습니다** — 같은 표에 있다는 이유로
  다른 사람의 변동을 클레임의 근거로 쓰지 마세요. 오직 "이용진" 본인의 수치 변화만
  이 클레임의 positive/negative를 결정합니다. 다른 사람의 변동은(그 자체로 클레임과
  무관하면) 애초에 뽑지 말거나, 관련이 있어 보여도(예: 가족 간 지분 이동) neutral로
  두세요.
  **"매도"(시장에 팔아 현금화)와 "증여·양도"(무상 이전, 상속·가족 간 이전 등)는 다른
  사건입니다.** 어떤 사람의 지분 감소가 "증여"·"양도"로 명시돼 있다면, 그건 "매도설"을
  뒷받침하는 근거가 아닙니다 — fact 문장에 그 감소의 실제 원인(증여/장내매도/시간외매매
  등 섹션에 쓰인 그대로)을 명시하고, 원인이 "매도"가 아니면 그 매도 클레임의 "negative"
  (오히려 매도가 아니었다는 반박)나 "neutral"로 판단하세요.
- is_confirmed: 이 사실이 **확정된 결정/사실**인지(이사회 결의, 계약 체결, 배당 결정 등
  이미 일어난 일), 아니면 **아직 확정되지 않은 상태**인지(검토 중, 확정된 바 없음, 협의
  중 등 섹션이 스스로 미확정이라고 밝힌 경우) — 섹션 원문에 "검토 중", "확정된 사항이
  없다", "협의 중" 같은 표현이 있으면 false로 표시한다.
- next_update_note: 섹션에 후속 공시 일정이 명시돼 있으면(예: "3개월 이내 재공시 예정",
  "확정되는 대로 공시") 그 내용을 그대로 옮긴다. 그런 언급이 없으면 null.

**중요**: 뒷받침하는 사실과 반박하는 사실을 동일한 성실함으로 찾으세요. 한쪽만 골라
나열하면 암묵적인 매수/매도 신호로 읽힙니다 — 이건 금지된 행동입니다."""

_GUARD_SYSTEM_PROMPT = """아래에 "클레임", "공시 섹션 목록", 그리고 그 목록에서 뽑았다고
주장하는 "사실 목록"이 주어집니다. 각 사실을 두 가지 기준으로 검사하세요.

1. 근거(groundedness): 그 사실이 실제로 섹션 목록의 내용에 있는가, 아니면 지어냈거나
   과장·왜곡했는가.
2. 관련성(relevance): 그 사실이 클레임이 주장하는 구체적인 사안과 실질적으로 관련
   있는가 — 단순히 "같은 회사에 관한 정보"라는 이유만으로는 관련 있다고 보지 않는다
   (이슈 #99 — 근거는 있어도 클레임과 무관한 사실이 "참고 사실"로 잘못 노출된 사례).

**특히 주의**: fact 문장 자체에 "무관하다/관련이 없다/별도의 사안이다/직접적인 관련이
없으나" 같은 표현이 있다면, 그건 사실을 뽑은 쪽(1차 추출)이 스스로 무관함을 인정하면서도
실수로 items에 남긴 경우입니다 — 그런 fact는 관련성 기준에서 반드시 탈락시키세요.
"클레임과 무관하다"고 쓰여 있는 fact를 관련 있다고 판단하면 안 됩니다.

두 기준을 모두 통과한 사실만 grounded_indices에 남기세요. 근거가 없거나 클레임과
무관한 사실은 제외하세요."""


class _FactCandidate(BaseModel):
    fact: str
    section_index: int | None
    polarity: Polarity
    is_confirmed: bool = True
    next_update_note: str | None = None


class _FactExtraction(BaseModel):
    items: list[_FactCandidate]


class _GroundednessCheck(BaseModel):
    grounded_indices: list[int]  # items 리스트 중 실제로 근거 있는 것들의 인덱스(0-based)


@dataclass
class FactCheckItem:
    fact: str
    supporting_section: DisclosureSection | None
    polarity: Polarity  # 동일 비중 노출을 위해 구조적으로 강제해둔다(PR #33 리뷰, @powercodingman)
    is_confirmed: bool = True  # False면 공시 자체가 "검토 중/미확정"이라고 밝힌 사실
    next_update_note: str | None = None  # 공시에 명시된 후속 공시 일정(예: "3개월 이내 재공시")


@dataclass
class FactCheckResult:
    claim: str
    items: list[FactCheckItem]
    # 투자 조언 표현 때문에 버려진 사실 수(compliance 필터). 0이 정상이다 — 값이 올라가면
    # 프롬프트가 지시를 어기고 있다는 신호다. 평가(run_eval)에서 "검색이 못 찾은 것"과
    # "필터가 지운 것"을 구분하려면 이 값이 필요하다(PR #115 리뷰 @9abin 제안).
    n_dropped_advice: int = 0
    status: Literal["ok", "abstained", "error"] = "ok"
    reason: (
        Literal[
            "no_sections",
            "low_relevance",
            "no_facts",
            "no_grounded_facts",
            "filtered_facts",
            "extraction_failed",
            "guard_failed",
            "unresolved_company",
        ]
        | None
    ) = None

    @property
    def abstained(self) -> bool:
        return self.status == "abstained"


# 실측(Issue #75, A: L5 run_eval.py로 gold set 27건 실행) — 정기공시(사업보고서/분기
# 보고서)의 "재무에 관한 사항" 섹션은 재무제표 표 전체가 한 섹션이라 최대 69만자까지
# 나온다. company_index.search()가 반환한 top_k(기본 10) 섹션을 그대로 프롬프트에
# 이어붙이면, 이 중 하나만 이런 거대 섹션이어도 프롬프트가 수십만~백만 토큰대로
# 부풀어 OpenAI TPM 한도(gpt-4o-mini 분당 20만)를 그 자체로 초과한다(재시도로도
# 해결 안 됨 — 27건 중 7건 실패 재현). retrieval 쪽에서 이런 섹션의 순위를 낮추는
# 게 근본 처방(Issue #75 코멘트, @jh3779 담당, company_index.search())이지만, 그걸
# 뚫고 들어온 경우까지 막는 방어선으로 여기서도 프롬프트에 넣는 텍스트 자체에 길이
# 상한을 건다. top_k 10건 전부 이 상한에 걸리는 가장 보수적인 경우도 총 4만자라,
# 한글 위주 텍스트가 1자당 1토큰을 넘는 경우가 드문 걸 감안하면 TPM 20만의 20%대라
# 여유가 있다. 임베딩 쪽 truncate(embed.py::MAX_EMBED_CHARS, PR #74)와 같은 원칙 —
# 저장되는 DisclosureSection.text 자체는 그대로 두고, 프롬프트 렌더링 단계에서만
# 자른다(supporting_section으로 반환되는 건 원본 섹션 그대로).
MAX_SECTION_CHARS = 4000


def _truncate_section_text(text: str) -> str:
    if len(text) <= MAX_SECTION_CHARS:
        return text
    return text[:MAX_SECTION_CHARS] + " …(이하 생략)"


def _render_sections(sections: list[DisclosureSection]) -> str:
    return "\n\n".join(
        f"[{i}] {s.section_title}\n{_truncate_section_text(s.text)}" for i, s in enumerate(sections)
    )


def _render_facts(items: list[_FactCandidate]) -> str:
    return "\n".join(f"{i}. {item.fact}" for i, item in enumerate(items))


def verify_claim(
    claim_keyword: str,
    sections: list[DisclosureSection],
    *,
    relevance_scores: list[float] | None = None,
    min_relevance_score: float | None = None,
) -> FactCheckResult:
    """클레임이 실제 공시로 뒷받침되는지 사실만 나열한다.

    `min_relevance_score`를 지정하면 `sections`와 같은 순서의 검색 점수가 필요하다.
    최고 점수가 문턱 미만이면 LLM 호출 전에 기권한다. 통과 시 원래 근거 목록을
    유지한다. 개별 섹션까지 걸러 버리면 검색 문턱 외에 컨텍스트도 달라지기 때문이다.
    기본값은 비활성화 — #130 실측에서 정상/실패 점수 분포가 겹쳤다.
    측정 결과와 한계: docs/testing/issue-130-abstention.json.

    narrative_qa `qa/chain.py::_guard`의 groundedness guard 패턴을 재사용한다 — 1차
    LLM 호출로 사실 목록 초안을 뽑고, 2차 LLM 호출로 그 사실들이 진짜 `sections`
    안에 있는 내용인지(근거) + 클레임과 실질적으로 관련 있는지(관련성) 재검증해서
    지어냈거나 무관한 것은 버린다(이슈 #99 — 근거는 있어도 클레임과 무관한 사실이
    "참고 사실"로 잘못 노출된 사례, `_EXTRACT_SYSTEM_PROMPT`/`_GUARD_SYSTEM_PROMPT`
    둘 다에 관련성 기준을 넣어 이중 방어). 등급·신호등 같은 종합 판단 지표는 출력하지
    않는다 — 자본시장법 방어선
    (docs/proposals/finance-pivot/03-integrated-proposal.md 7절).
    """
    if min_relevance_score is not None:
        if not math.isfinite(min_relevance_score):
            raise ValueError("min_relevance_score must be finite")
        if relevance_scores is None or len(relevance_scores) != len(sections):
            raise ValueError("relevance_scores must align with sections")
        if not all(math.isfinite(score) for score in relevance_scores):
            raise ValueError("relevance_scores must be finite")
    if not sections:
        return FactCheckResult(
            claim=claim_keyword, items=[], status="abstained", reason="no_sections"
        )
    if min_relevance_score is not None and max(relevance_scores) < min_relevance_score:
        return FactCheckResult(
            claim=claim_keyword, items=[], status="abstained", reason="low_relevance"
        )

    sections_block = _render_sections(sections)

    draft = complete(
        _EXTRACT_SYSTEM_PROMPT,
        f"클레임: {claim_keyword}\n\n공시 섹션 목록:\n{sections_block}",
        tier="final",
        schema=_FactExtraction,
    )
    if draft.parsed is None:
        # 파싱 실패를 근거 부족과 구분한다(#130). 원본 응답은 진단 로그로만 남긴다.
        logger.warning(
            "verify_claim: 1차 사실 추출 파싱 실패(claim=%r) — 원본 응답: %.200r",
            claim_keyword,
            draft.text,
        )
        return FactCheckResult(
            claim=claim_keyword, items=[], status="error", reason="extraction_failed"
        )
    candidates = draft.parsed.items  # draft.parsed is None 은 위에서 반환됨
    if not candidates:
        return FactCheckResult(claim=claim_keyword, items=[], status="abstained", reason="no_facts")

    guard = complete(
        _GUARD_SYSTEM_PROMPT,
        f"클레임: {claim_keyword}\n\n공시 섹션 목록:\n{sections_block}\n\n"
        f"사실 목록:\n{_render_facts(candidates)}",
        tier="mini",
        schema=_GroundednessCheck,
    )
    # guard 호출 자체가 실패하면(파싱 불가 등) 안전한 쪽으로 — 아무것도 통과시키지 않는다.
    if guard.parsed is None:
        logger.warning(
            "verify_claim: 2차 groundedness guard 파싱 실패(claim=%r) — 원본 응답: %.200r",
            claim_keyword,
            guard.text,
        )
        return FactCheckResult(claim=claim_keyword, items=[], status="error", reason="guard_failed")
    grounded_indices = set(guard.parsed.grounded_indices)  # 위에서 None 반환 처리됨

    items = [
        FactCheckItem(
            fact=candidate.fact,
            supporting_section=(
                sections[candidate.section_index]
                if candidate.section_index is not None
                and 0 <= candidate.section_index < len(sections)
                else None
            ),
            polarity=candidate.polarity,
            is_confirmed=candidate.is_confirmed,
            next_update_note=candidate.next_update_note,
        )
        for i, candidate in enumerate(candidates)
        if i in grounded_indices
    ]
    kept = _drop_investment_advice(items)
    return FactCheckResult(
        claim=claim_keyword,
        items=kept,
        n_dropped_advice=len(items) - len(kept),
        status="ok" if kept else "abstained",
        reason=None if kept else ("filtered_facts" if items else "no_grounded_facts"),
    )


def _drop_investment_advice(items: list[FactCheckItem]) -> list[FactCheckItem]:
    """투자 조언 표현이 섞인 사실을 버린다 — 규제 방어선의 **결정적** 마지막 단계.

    위의 두 LLM 호출(추출 지시 + guard)은 확률적 방어라, 지시를 어긴 출력이 guard를
    통과할 수 있다(guard는 근거성·관련성만 본다). `compliance.py` 모듈 docstring 참고 —
    경계는 코퍼스 실측으로 "원문에 0회 나오는 조언 발화"로만 좁혀 두었다.

    polarity와 무관하게 똑같이 적용한다 — 한쪽만 거르면 그 자체로 비중이 기운다.
    버린 항목은 반드시 로그로 남긴다(조용히 사라지면 프롬프트 문제를 추적할 수 없다).

    **화면에 나가는 LLM 텍스트는 전부 검사한다** — `fact`뿐 아니라 PR #114가 추가한
    `next_update_note`도 LLM이 만들어 화면에 그대로 표시된다(demo/app.py 📅 캡션).
    다만 둘의 무게가 달라 처리도 다르게 한다: `fact`에 조언이 섞이면 사실 전체를
    버리지만, 보조 정보인 `next_update_note`에만 섞였으면 **메모만 지우고 사실은
    살린다** — 멀쩡한 공시 사실을 부가 메모 하나 때문에 잃을 이유가 없다.
    """
    kept: list[FactCheckItem] = []
    for item in items:
        advice = find_investment_advice(item.fact)
        if advice is not None:
            logger.warning(
                "verify_claim: 투자 조언 표현(%r)이 섞인 사실을 제외함 — polarity=%s, fact=%.120r",
                advice,
                item.polarity,
                item.fact,
            )
            continue
        if item.next_update_note:
            note_advice = find_investment_advice(item.next_update_note)
            if note_advice is not None:
                logger.warning(
                    "verify_claim: next_update_note에 투자 조언 표현(%r) — 메모만 제거함: %.120r",
                    note_advice,
                    item.next_update_note,
                )
                item = replace(item, next_update_note=None)
        kept.append(item)
    return kept
