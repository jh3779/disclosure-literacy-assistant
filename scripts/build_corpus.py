"""A10 — 선정된 종목 전체에 대해 공시 대량 수집. 담당: A(이승노).

select_corpus_companies.py 가 만든 data/raw/corpus_companies.json 을 읽어 종목을
순회하며, collect_disclosures.py(A8)의 collect_for_company() 를 그대로 재사용해
list_disclosures -> fetch_document -> chunk_disclosure 파이프라인을 각 종목에
적용한다.

기간: 종료일 = 오늘 - lookback_months(기본 6개월, 사후 이벤트 관찰 여유 확보 —
A12 사후이벤트 라벨링이 "공시 이후 N개월 내 무슨 일이 있었는지"를 봐야 하는데
너무 최근 공시는 아직 그 시간이 없다), 시작일 = 종료일 - years(기본 2년).

재실행 안전: collect_for_company() 가 이미 처리된 rcept_no 를 건너뛰므로, 중간에
끊겨도 다시 실행하면 이어서 진행된다.

종목 단위 실패 격리: 한 종목의 공시 목록 조회(list.json)가 DART 오류·네트워크 오류로
실패해도 배치 전체를 멈추지 않고 다음 종목으로 넘어간다 — 멈추면 재실행해도 매번 같은
종목에서 다시 죽어서 그 뒤 종목들이 영구히 수집되지 않는다. 실패 종목은 마지막에 모아
출력한다. 코드 버그(KeyError 등)는 일부러 잡지 않는다 — 100개사에 걸쳐 조용히 건너뛰면
버그를 못 알아챈다.

실행:
    uv run python scripts/build_corpus.py
    uv run python scripts/build_corpus.py --years 3 --lookback-months 12
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import httpx

# collect_disclosures.py 와 같은 디렉터리 스크립트를 모듈처럼 임포트
from collect_disclosures import (
    FailureRecord,
    collect_for_company,
    fix_console_encoding,
    write_failures_jsonl,
)

from disclosure_lit.config import settings
from disclosure_lit.dart.client import DartApiError, DartClient

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_COMPANIES_FILE = PROJECT_ROOT / "data" / "raw" / "corpus_companies.json"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--companies-file", default=None, help="select_corpus_companies.py 결과 파일 경로"
    )
    parser.add_argument("--years", type=float, default=2, help="수집 기간 길이(년, 기본 2)")
    parser.add_argument(
        "--lookback-months",
        type=int,
        default=6,
        help="종료일을 오늘로부터 몇 개월 전으로 잡을지(기본 6 — 사후이벤트 관찰 여유)",
    )
    parser.add_argument("--force", action="store_true", help="종목별로 전체 재처리")
    parser.add_argument(
        "--limit", type=int, default=None, help="처음 N개사만 처리(테스트/부분 실행용)"
    )
    return parser.parse_args()


def _date_range(years: float, lookback_months: int) -> tuple[str, str]:
    end = date.today() - timedelta(days=lookback_months * 30)
    bgn = end - timedelta(days=int(years * 365))
    return bgn.strftime("%Y%m%d"), end.strftime("%Y%m%d")


def main() -> None:
    fix_console_encoding()
    args = _parse_args()
    companies_path = Path(args.companies_file) if args.companies_file else DEFAULT_COMPANIES_FILE
    bgn_de, end_de = _date_range(args.years, args.lookback_months)

    companies = json.loads(companies_path.read_text(encoding="utf-8"))
    if args.limit is not None:
        companies = companies[: args.limit]
    print(f"대상 {len(companies)}개사, 기간 {bgn_de}~{end_de}")

    client = DartClient(settings.dart_api_key)

    t0 = time.time()
    totals = {"ok": 0, "skipped": 0, "failed": 0, "disclosures": 0}
    failed_companies: list[tuple[str, str, str]] = []
    all_failures: list[FailureRecord] = []
    for i, company in enumerate(companies, 1):
        corp_code = company["corp_code"]
        corp_name = company["corp_name"]
        elapsed = time.time() - t0
        print(f"\n=== [{i}/{len(companies)}] {corp_name} ({corp_code}) — 경과 {elapsed:.0f}초 ===")

        try:
            stats = collect_for_company(
                client, corp_code, bgn_de=bgn_de, end_de=end_de, force=args.force, verbose=False
            )
        except (DartApiError, httpx.HTTPError) as exc:
            # DartRateLimitError 는 DartApiError 하위라 재시도(@retry) 소진 후에도 여기로 온다.
            failed_companies.append((corp_code, corp_name, f"{type(exc).__name__}: {exc}"))
            print(f"  종목 실패 — 건너뛰고 계속: {type(exc).__name__}: {exc}", file=sys.stderr)
            continue
        totals["ok"] += stats.ok
        totals["skipped"] += stats.skipped
        totals["failed"] += stats.failed
        totals["disclosures"] += stats.total_disclosures
        all_failures.extend(stats.failures)
        print(
            f"  공시 {stats.total_disclosures}건 — 성공 {stats.ok}, "
            f"건너뜀 {stats.skipped}, 실패 {stats.failed}"
        )

    # companies_path 와 같은 디렉터리에 둔다 — PROJECT_ROOT 에 고정하면 --companies-file
    # 로 다른 배치를 돌릴 때도 항상 같은 파일을 덮어써 실행 간 실패 기록이 섞인다.
    failures_path = companies_path.parent / "failures.jsonl"
    write_failures_jsonl(failures_path, all_failures)

    elapsed = time.time() - t0
    print(
        f"\n{'=' * 60}\n전체 완료 — {len(companies)}개사, {elapsed / 60:.1f}분 소요\n"
        f"공시 총 {totals['disclosures']}건 — 성공 {totals['ok']}, "
        f"건너뜀 {totals['skipped']}, 실패 {totals['failed']}"
    )
    if all_failures:
        print(f"공시 단위 실패 상세({len(all_failures)}건): {failures_path}", file=sys.stderr)
    if failed_companies:
        print(
            f"\n종목 단위 실패 {len(failed_companies)}개사 — 재실행하면 이 종목들만 다시 시도한다"
            "(이미 수집된 공시는 건너뜀):",
            file=sys.stderr,
        )
        for corp_code, corp_name, reason in failed_companies:
            print(f"  {corp_name} ({corp_code}): {reason}", file=sys.stderr)


if __name__ == "__main__":
    main()
