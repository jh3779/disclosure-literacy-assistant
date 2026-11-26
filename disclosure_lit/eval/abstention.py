"""#130 검색 점수 분포와 기권 전후 반복 평가.

python -m disclosure_lit.eval.abstention --threshold 0.47060567140579224 --runs 5 --workers 4
점수 분포만 확인하려면 --scores-only. 원문은 결과 파일에 저장하지 않는다.

baseline은 점수 문턱이 없는 기존 추출/검증 경로에 상태 계측만 추가한 것이다.
빈 추출/guard 탈락도 기권으로 집계하므로, 과거 문서의 기권율 0%와 정의가 다르다.
pre-extraction 기권은 사례별 abstention_reason이 no_sections/low_relevance인 건이다.
평가는 전체 루머와 top_k=10을 쓰며, UI의 추출 키워드/top_k=5와는 조건이 다르다.
위 문턱은 실험 후보이지 제품 권장값이 아니다. 결과: docs/testing/issue-130-abstention.json.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path

from disclosure_lit.cache_store import company_fingerprint, load_company_index
from disclosure_lit.config import PROJECT_ROOT, settings
from disclosure_lit.dictionary.candidates import load_all_local_sections
from disclosure_lit.eval.console import fix_console_encoding
from disclosure_lit.eval.gold_schema import DEFAULT_GOLD_PATH, load_gold_cases
from disclosure_lit.eval.run_eval import CaseResult, evaluate_case, summarize
from disclosure_lit.retrieval.company_index import (
    DEFAULT_MIN_SCORE,
    CompanyDisclosureIndex,
    SearchHit,
)


class PreparedSearch:
    """검색 결과를 고정해 모든 회차/버전에 동일한 후보를 제공한다."""

    def __init__(self, hits: dict[tuple[str, str], list[SearchHit]]) -> None:
        self.hits = hits

    def search_with_scores(self, corp_code: str, keyword: str, *, top_k: int = 10):
        return [h for h in self.hits[corp_code, keyword] if h.score >= DEFAULT_MIN_SCORE][:top_k]

    def search(self, corp_code: str, keyword: str, *, top_k: int = 10):
        return [h.section for h in self.search_with_scores(corp_code, keyword, top_k=top_k)]


def run_experiment(
    *,
    gold_path: Path,
    raw_dir: Path,
    output: Path,
    threshold: float | None,
    runs: int = 5,
    workers: int = 1,
    scores_only: bool = False,
) -> dict:
    if runs < 1 or workers < 1:
        raise ValueError("runs and workers must be positive")
    if threshold is not None and not math.isfinite(threshold):
        raise ValueError("threshold must be finite")
    cases = load_gold_cases(gold_path)
    if not cases:
        raise ValueError("gold set is empty")
    sections = load_all_local_sections(raw_dir)
    if not sections:
        raise ValueError("corpus is empty; cannot measure a threshold")
    fingerprint = company_fingerprint(sections)
    index = load_company_index(PROJECT_ROOT / "data/cache", fingerprint)
    if index is None:
        index = CompanyDisclosureIndex()
        codes = {case.corp_code for case in cases}
        index.index([section for section in sections if section.corp_code in codes])
    print(f"Corpus {fingerprint}: {len(sections)} sections", flush=True)
    prepared = PreparedSearch(
        {
            (case.corp_code, case.rumor_text): index.search_with_scores(
                case.corp_code, case.rumor_text, min_score=float("-inf")
            )
            for case in cases
        }
    )
    report = {
        "corpus_fingerprint": fingerprint,
        "gold_sha256": hashlib.sha256(gold_path.read_bytes()).hexdigest(),
        "provider": settings.llm_provider,
        "model_mini": settings.model_mini,
        "model_final": settings.model_final,
        "embed_backend": settings.embed_backend,
        "embed_model": (
            settings.local_embed_model
            if settings.embed_backend == "local"
            else settings.embed_model
        ),
        "threshold": threshold,
        "requested_runs": runs,
        "workers": workers,
        "profiles": [
            {
                "corp_code": case.corp_code,
                "rumor_text": case.rumor_text,
                "source_url": case.source_url,
                "hits": [
                    {"score": hit.score, "rcept_no": hit.section.rcept_no}
                    for hit in prepared.hits[case.corp_code, case.rumor_text]
                ],
            }
            for case in cases
        ],
        "runs": [],
    }
    output.parent.mkdir(parents=True, exist_ok=True)

    def save():
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    save()
    if scores_only:
        return report
    variants = [("baseline", None)]
    if threshold is not None:
        variants.append(("candidate", threshold))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for run_number in range(1, runs + 1):
            for variant, cutoff in variants:
                records = []
                futures = {
                    pool.submit(
                        evaluate_case, case, company_index=prepared, min_relevance_score=cutoff
                    ): i
                    for i, case in enumerate(cases)
                }
                run = {"run": run_number, "variant": variant, "cases": records, "complete": False}
                report["runs"].append(run)
                for future in as_completed(futures):
                    i = futures[future]
                    try:
                        records.append({"case_index": i, "result": asdict(future.result())})
                    except Exception as exc:
                        records.append({"case_index": i, "error": type(exc).__name__})
                    records.sort(key=lambda record: record["case_index"])
                    save()
                    print(f"Run {run_number} {variant}: {len(records)}/{len(cases)}", flush=True)
                successes = [CaseResult(**r["result"]) for r in records if "result" in r]
                run["summary"] = summarize(successes)
                run["n_failed"] = len(cases) - len(successes)
                run["complete"] = not run["n_failed"]
                save()
    return report


def main() -> None:
    fix_console_encoding()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD_PATH)
    parser.add_argument("--raw-dir", type=Path, default=PROJECT_ROOT / "data/raw")
    parser.add_argument(
        "--output", type=Path, default=PROJECT_ROOT / "eval/results/abstention.json"
    )
    parser.add_argument("--threshold", type=float)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--scores-only", action="store_true")
    args = parser.parse_args()
    report = run_experiment(
        gold_path=args.gold,
        raw_dir=args.raw_dir,
        output=args.output,
        threshold=args.threshold,
        runs=args.runs,
        workers=args.workers,
        scores_only=args.scores_only,
    )
    print(f"Saved: {args.output}")
    if any(not run["complete"] for run in report["runs"]):
        raise SystemExit("Incomplete evaluation: inspect failed cases before comparing metrics")


if __name__ == "__main__":
    main()
