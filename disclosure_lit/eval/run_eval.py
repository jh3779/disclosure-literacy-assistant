"""gold set 대비 팩트체크 정밀도·재현율 측정 — L5. REQ-F-008.

각 gold 사례(`RumorGoldCase`)에 대해 실제 파이프라인(`CompanyDisclosureIndex.search` →
`verify_claim`)을 돌리고, 결과를 LLM judge(`eval/judges.py`)로 채점한다:

- recall: 기대 사실(`expected_facts`) 중 몇 개를 실제로 찾아냈는가
- hallucination_rate: 실제로 낸 사실 중 근거 없이 지어낸 게 몇 개인가(0이어야 이상적 —
  docs/06_open_questions.md 최고 심각도 리스크와 직결)

`corp_code`는 gold 데이터에 이미 있어서 `extract_claim`/`resolve_corp_code`(종목명→코드
해석)는 여기서 거치지 않는다 — 이 스크립트는 L2a 검색+검증 품질만 측정하는 것이 목적이라,
클레임 추출 단계의 오차까지 섞으면 어느 단계가 문제인지 구분이 안 된다.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from disclosure_lit.config import PROJECT_ROOT
from disclosure_lit.dictionary.candidates import load_all_local_sections
from disclosure_lit.eval.console import fix_console_encoding
from disclosure_lit.eval.gold_schema import DEFAULT_GOLD_PATH, RumorGoldCase, load_gold_cases
from disclosure_lit.eval.judges import judge_hallucination, judge_recall
from disclosure_lit.factcheck.verify import verify_claim
from disclosure_lit.retrieval.company_index import CompanyDisclosureIndex

DEFAULT_RAW_DIR = PROJECT_ROOT / "data" / "raw"
_SEARCH_TOP_K = 10


@dataclass
class CaseResult:
    rumor_text: str
    corp_code: str
    n_actual_facts: int
    recall: float
    hallucination_rate: float
    # compliance 필터가 버린 사실 수 — recall이 낮을 때 "검색이 근거를 못 찾은 것"인지
    # "필터가 지운 것"인지 구분하는 데 쓴다(PR #115 리뷰 @9abin 제안). 0이 정상.
    n_dropped_advice: int = 0
    abstained: bool = False
    abstention_reason: str | None = None
    # 엔진 실패(status="error") — 기권과 구분해서 센다. 0점으로 채점되므로 평균에는
    # 들어가지만, 이 비율이 높으면 "검색이 못 찾은 것"이 아니라 "엔진이 깨진 것"이다.
    failed: bool = False
    failure_reason: str | None = None


def build_company_index(
    raw_dir: Path = DEFAULT_RAW_DIR, *, corp_codes: set[str] | None = None
) -> CompanyDisclosureIndex:
    """`data/raw/`의 로컬 코퍼스를 인덱싱한다.

    `corp_codes`를 주면 그 회사들의 섹션만 임베딩한다. `search()`가 어차피
    corp_code로 후보를 좁히므로 **채점 결과는 전체를 인덱싱한 것과 동일**하고,
    쓰지도 않을 회사를 임베딩하는 비용만 사라진다 — 실측(2026-09-22, 114개사
    코퍼스): 전체 82,876섹션을 인덱싱하면 40분대인데, 골드셋에 등장하는 14개사
    16,071섹션만 하면 약 7분이다. `run()`이 골드 사례의 corp_code를 넘겨 쓴다.

    코퍼스가 없으면(수집 전) 빈 인덱스 — `verify_claim`이 빈 후보 목록을 받아 즉시
    빈 결과를 반환하므로 에러 없이 recall=0으로 채점된다(candidates.py와 동일 원칙,
    "없으면 없는 대로" — 에러 대신 정직한 낮은 점수).
    """
    index = CompanyDisclosureIndex()
    sections = load_all_local_sections(raw_dir)
    if corp_codes is not None:
        sections = [s for s in sections if s.corp_code in corp_codes]
    if sections:
        index.index(sections)
    return index


def evaluate_case(
    case: RumorGoldCase,
    *,
    company_index: CompanyDisclosureIndex,
    min_relevance_score: float | None = None,
) -> CaseResult:
    if min_relevance_score is None:
        candidates = company_index.search(case.corp_code, case.rumor_text, top_k=_SEARCH_TOP_K)
        result = verify_claim(case.rumor_text, candidates)
    else:
        hits = company_index.search_with_scores(
            case.corp_code, case.rumor_text, top_k=_SEARCH_TOP_K
        )
        result = verify_claim(
            case.rumor_text,
            [hit.section for hit in hits],
            relevance_scores=[hit.score for hit in hits],
            min_relevance_score=min_relevance_score,
        )
    # 엔진 실패(LLM 응답 파싱 실패 등)를 예외로 올리면 evaluate_all()이 그 사례를
    # 결과 목록에서 통째로 빼버린다 — 재현율 0점짜리 사례만 골라 분모에서 빠지므로
    # 평균 재현율이 조용히 올라간다(실측: 4건 중 1건 실패 시 75.0% -> 100.0%).
    # 화면에서 사용자가 아무 사실도 못 받는 상황이니 0점으로 채점하는 게 정직하다.
    # 실패 자체는 `failed`/`failure_reason`으로 따로 세어 summarize()가 보고한다.
    actual_facts = [item.fact for item in result.items]

    recall_verdict = judge_recall(case.expected_facts, actual_facts)
    hallucination_verdict = judge_hallucination(case.expected_facts, actual_facts)

    return CaseResult(
        rumor_text=case.rumor_text,
        corp_code=case.corp_code,
        n_actual_facts=len(actual_facts),
        n_dropped_advice=result.n_dropped_advice,
        recall=recall_verdict.recall,
        hallucination_rate=hallucination_verdict.hallucination_rate,
        abstained=result.abstained,
        abstention_reason=result.reason if result.abstained else None,
        failed=result.status == "error",
        failure_reason=result.reason if result.status == "error" else None,
    )


def evaluate_all(
    cases: list[RumorGoldCase], *, company_index: CompanyDisclosureIndex
) -> list[CaseResult]:
    """gold 사례 전체를 채점한다. 케이스 단위로 실패를 격리한다.

    build_corpus.py의 종목 단위 실패 격리(커밋 a984dee)와 같은 원칙 — 27건 중 한 건이
    LLM 호출 실패(타임아웃·레이트리밋 등)로 죽으면 이미 채점된 나머지 결과까지 전부
    유실되던 문제(PR #53 리뷰, @9abin 지적)를 고친다. 실패한 사례는 stderr로 보고하고
    계속 진행한다 — 코드 버그까지 조용히 삼키면 알아챌 수 없으므로, 예외 종류를 가리지
    않고 잡되 반드시 로그를 남긴다.
    """
    results: list[CaseResult] = []
    failed: list[tuple[RumorGoldCase, str]] = []
    for case in cases:
        try:
            results.append(evaluate_case(case, company_index=company_index))
        except Exception as exc:  # noqa: BLE001 — LLM/네트워크 실패 케이스를 격리
            failed.append((case, f"{type(exc).__name__}: {exc}"))
            print(
                f"  케이스 실패 — 건너뛰고 계속: {case.corp_code} {case.rumor_text[:30]}... "
                f"({type(exc).__name__}: {exc})",
                file=sys.stderr,
            )
    if failed:
        print(
            f"\n채점 실패 {len(failed)}건(전체 {len(cases)}건 중) — 위 stderr 로그 참고",
            file=sys.stderr,
        )
    return results


def summarize(results: list[CaseResult]) -> dict[str, float]:
    if not results:
        return {
            "n": 0,
            "mean_recall": 0.0,
            "mean_hallucination_rate": 0.0,
            "n_dropped_advice": 0,
            "abstention_rate": 0.0,
            "failure_rate": 0.0,
        }
    return {
        "n": len(results),
        "mean_recall": sum(r.recall for r in results) / len(results),
        "mean_hallucination_rate": sum(r.hallucination_rate for r in results) / len(results),
        "n_dropped_advice": sum(r.n_dropped_advice for r in results),
        "abstention_rate": sum(r.abstained for r in results) / len(results),
        "failure_rate": sum(r.failed for r in results) / len(results),
    }


def run(gold_path: Path = DEFAULT_GOLD_PATH, raw_dir: Path = DEFAULT_RAW_DIR) -> None:
    fix_console_encoding()
    cases = load_gold_cases(gold_path)
    if not cases:
        print(f"gold set이 비어있습니다: {gold_path}", file=sys.stderr)
        return

    gold_corp_codes = {case.corp_code for case in cases}
    print(
        f"골드셋 {len(cases)}건 / 대상 회사 {len(gold_corp_codes)}개 — 인덱싱 시작"
        " (채점에 쓰이는 회사만 임베딩한다, build_company_index() docstring 참고)",
        flush=True,
    )
    company_index = build_company_index(raw_dir, corp_codes=gold_corp_codes)
    print("인덱싱 완료 — LLM 채점 시작", flush=True)

    results = evaluate_all(cases, company_index=company_index)
    summary = summarize(results)

    print(f"{'회사':10} {'재현율':>8} {'지어냄':>8}  루머")
    for r in results:
        print(
            f"{r.corp_code:10} {r.recall:>7.0%} {r.hallucination_rate:>7.0%}  {r.rumor_text[:40]}"
        )
    print()
    print(
        f"전체 {summary['n']}건 — 평균 재현율 {summary['mean_recall']:.0%}, "
        f"평균 hallucination {summary['mean_hallucination_rate']:.0%}, "
        f"기권율 {summary['abstention_rate']:.0%}, "
        f"엔진 실패율 {summary['failure_rate']:.0%}"
    )
    print("목표(REQ-F-008): 정밀도·재현율 80%+ — hallucination_rate는 0%에 가까울수록 좋음")
    dropped = summary["n_dropped_advice"]
    if dropped:
        # 0이 정상이다. 0이 아니면 프롬프트가 "투자 조언 금지" 지시를 어기고 있다는 뜻이고,
        # 재현율이 낮을 때 그 원인이 검색이 아니라 필터일 수 있다는 신호다.
        print(
            f"⚠️ compliance 필터가 버린 사실 {dropped}건 — 투자 조언 표현이 섞여 있었다는"
            " 뜻이다(로그 확인). 재현율 해석 시 감안할 것."
        )


if __name__ == "__main__":
    run()
