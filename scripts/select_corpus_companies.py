"""A10 — 코퍼스 구축 대상 종목 선정. 담당: A(이승노).

corpCode.xml 에서 상장 후보를 뽑고, company.json 으로 시장구분(코스피·코스닥만,
코넥스 제외 — docs/DESIGN.md "확정 파라미터")과 업종코드를 확인한 뒤, 업종별로
고르게 섞이도록 라운드로빈 방식으로 최종 코퍼스 대상 종목을 선정한다. 결과를
data/raw/corpus_companies.json 에 저장해서 재현 가능하게 한다.

**2026-09-17 추가(코퍼스 "확장"이 아니라 "변동" — 회사 수만 늘려서는 부족하다는 판단)**:
1. 기존에 이미 선정된 회사는 기본적으로 후보에서 제외한다(`--exclude-companies-file`,
   기본값이 바로 `corpus_companies.json` 자기 자신) — 재실행할 때마다 신규 회사만
   추가되도록.
2. 업종만 다양하게 섞는 기존 라운드로빈에 "희귀 사후 이벤트 이력" 가중치를 더한다
   (`--rare-event-lookback-years`, 기본 3). 업종만 다양화하면 어느 회사가 실제로
   `제재_벌금`·`전환사채_매도`처럼 코퍼스에 드문 이벤트를 겪었는지는 순전히 운에
   맡겨진다 — `data/raw/post_event_labels.jsonl`(A12 결과, 있으면)을 읽어 지금
   코퍼스에서 유독 표본이 적은 outcome 유형을 찾고, 그 유형에 해당하는 공시를
   과거에 낸 적 있는 후보를 업종 버킷 안에서 우선 뽑는다. `--skip-rare-event-scoring`
   으로 끌 수 있다(후보마다 API 호출이 하나 더 들어서 선정 시간이 늘어남).

실행:
    uv run python scripts/select_corpus_companies.py --target 100
    uv run python scripts/select_corpus_companies.py --target 300 \
        --exclude-companies-file data/raw/corpus_companies.json
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import httpx

# collect_disclosures.py/label_post_events.py 와 같은 디렉터리 스크립트를 모듈처럼 임포트
from collect_disclosures import fix_console_encoding
from label_post_events import OUTCOME_FORM_TYPES

from disclosure_lit.config import settings
from disclosure_lit.dart.client import CORP_CLASS_KOSDAQ, CORP_CLASS_KOSPI, DartApiError, DartClient
from disclosure_lit.dart.ingest import normalize_form_type

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CORP_CODE_CACHE = PROJECT_ROOT / "data" / "raw" / "corp_code.zip"
DEFAULT_OUT = PROJECT_ROOT / "data" / "raw" / "corpus_companies.json"
DEFAULT_POST_EVENT_LABELS = PROJECT_ROOT / "data" / "raw" / "post_event_labels.jsonl"

TARGET_MARKETS = {CORP_CLASS_KOSPI, CORP_CLASS_KOSDAQ}  # 코넥스(N)·기타(E) 제외

# Issue #47 — corp_code.zip 캐시에 나이 확인 없이 무기한 재사용하던 것을 고친다.
# 정확한 갱신 주기가 알려진 값이 아니라(DART 쪽 공식 문서 없음) 느슨한 경고용
# 문턱값이다 — 이 기간을 넘겼다고 캐시가 반드시 stale하다는 보장은 아니다.
CORP_CODE_CACHE_STALE_DAYS = 7


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--target", type=int, default=100, help="최종 선정할 종목 수(기본 100)")
    parser.add_argument(
        "--candidate-pool", type=int, default=400, help="후보 풀 크기(기본 400, API 비용 관리용)"
    )
    parser.add_argument("--seed", type=int, default=42, help="무작위 후보 추출 시드(재현용)")
    parser.add_argument(
        "--out", default=None, help="결과 저장 경로(기본 data/raw/corpus_companies.json)"
    )
    parser.add_argument(
        "--refresh-cache",
        action="store_true",
        help="corp_code.zip 캐시를 무시하고 새로 내려받는다(신규 상장·상장폐지 반영)",
    )
    parser.add_argument(
        "--exclude-companies-file",
        default=None,
        help="이미 선정된 회사 목록(있으면 후보에서 제외, 기본값: --out과 동일 경로)",
    )
    parser.add_argument(
        "--no-exclude-existing",
        action="store_true",
        help="이미 선정된 회사도 다시 후보로 허용한다(기본은 제외)",
    )
    parser.add_argument(
        "--skip-rare-event-scoring",
        action="store_true",
        help="희귀 사후 이벤트 이력 가중치를 끈다(후보당 API 호출 하나 줄어 더 빠름)",
    )
    parser.add_argument(
        "--rare-event-lookback-years",
        type=float,
        default=3,
        help="희귀 이벤트 이력을 몇 년치 조회해서 판단할지(기본 3)",
    )
    parser.add_argument(
        "--post-event-labels-file",
        default=None,
        help="희귀 유형 판단 기준 파일(기본 post_event_labels.jsonl, 없으면 스코어링 skip)",
    )
    return parser.parse_args()


def _load_excluded_corp_codes(path: Path) -> set[str]:
    """이미 선정된 회사 목록에서 corp_code만 뽑는다. 파일이 없으면(처음 선정하는
    경우) 빈 집합 — 제외할 게 없을 뿐이지 에러는 아니다."""
    if not path.exists():
        return set()
    companies = json.loads(path.read_text(encoding="utf-8"))
    return {c["corp_code"] for c in companies}


def _rare_outcome_types(post_event_labels_path: Path, *, bottom_fraction: float = 0.5) -> set[str]:
    """`post_event_labels.jsonl`(A12)에서 지금 코퍼스가 유독 표본이 적은 outcome 유형을
    찾는다 — `OUTCOME_FORM_TYPES`(label_post_events.py) 전체 중 관측 회사 수 하위
    `bottom_fraction`을 "희귀"로 본다. 파일이 없으면(아직 라벨링 전) 빈 집합 — 이 경우
    호출부가 가중치 없이 기존 업종 다양성만으로 선정한다.

    라벨 건수가 아니라 "그 유형을 한 번이라도 겪은 회사 수"로 판단한다 —
    dictionary/stats.py의 회사 단위 중복 제거와 같은 원칙(한 회사의 반복 제출이
    희귀도 판단을 왜곡하면 안 된다).
    """
    if not post_event_labels_path.exists():
        return set()
    company_outcomes: dict[str, set[str]] = defaultdict(set)
    with post_event_labels_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if row.get("is_censored"):
                continue
            for event in row.get("outcome_events", []):
                company_outcomes[row["corp_code"]].add(event)

    counts = {outcome: 0 for outcome in OUTCOME_FORM_TYPES}
    for outcomes in company_outcomes.values():
        for outcome in outcomes:
            if outcome in counts:
                counts[outcome] += 1

    ranked = sorted(OUTCOME_FORM_TYPES, key=lambda o: counts[o])
    cutoff = max(1, int(len(ranked) * bottom_fraction))
    return set(ranked[:cutoff])


def _rare_event_score(
    client: DartClient, corp_code: str, rare_types: set[str], *, years: float
) -> int:
    """이 회사가 과거 `years`년 동안 `rare_types`에 속하는 유형의 공시를 몇 종류 냈는지
    센다(건수가 아니라 "겪은 유형의 개수" — 한 유형을 여러 번 낸 것보다, 서로 다른
    희귀 유형을 여러 개 겪은 회사가 코퍼스 다양성에 더 기여한다고 봤다).

    조회 실패(네트워크 오류 등)는 이 후보만 가중치 0으로 두고 계속 진행한다 — 선정
    단계에서 회사 하나 조회가 실패했다고 전체를 멈추면 안 된다(다른 A10 스크립트들과
    같은 원칙, collect_disclosures.py 모듈 docstring 참고).
    """
    end = date.today()
    bgn = end - timedelta(days=int(years * 365))
    try:
        metas = client.list_disclosures(
            corp_code, bgn_de=bgn.strftime("%Y%m%d"), end_de=end.strftime("%Y%m%d")
        )
    except (DartApiError, httpx.HTTPError):
        return 0
    hit_types = {normalize_form_type(m.report_nm) for m in metas}
    return len(hit_types & rare_types)


def _warn_if_corp_code_cache_stale(cache_path: Path) -> None:
    """corp_code.zip 캐시가 오래됐으면 경고한다(Issue #47).

    캐시 로직(`_fetch_zip_or_raise`, disclosure_lit/dart/client.py)이 파일이 있으면
    나이·수정일 확인 없이 무조건 그대로 반환한다 — 몇 주~몇 달 뒤 코퍼스를 보강/
    재추출할 때도 그대로 재사용돼, 그사이 신규 상장된 종목은 후보에 아예 안 뜨고
    상장폐지된 종목은 계속 후보로 남는데 이걸 알려주는 경고가 전혀 없었다. 여기서는
    캐시를 강제로 무효화하지 않고 경고만 한다 — DART가 얼마나 자주 갱신하는지
    공식적으로 알려진 바가 없어서, 오래됐다고 단정하기보다 사용자가 판단하게 한다.
    """
    if not cache_path.exists():
        return
    age_days = (time.time() - cache_path.stat().st_mtime) / 86400
    if age_days >= CORP_CODE_CACHE_STALE_DAYS:
        print(
            f"경고: corp_code.zip 캐시가 {age_days:.0f}일 전 것입니다 — 그사이 신규 상장·"
            "상장폐지가 반영 안 됐을 수 있습니다. --refresh-cache 로 새로 받을 수 있습니다.",
            file=sys.stderr,
        )


def _industry_bucket(induty_code: str) -> str:
    """업종코드 앞 2자리를 대분류 근사치로 쓴다.

    정확한 KSIC 대분류 매핑표 없이도 "전부 같은 업종"으로 쏠리는 걸 막는 정도의
    조악한 그룹핑이면 이번 표본 추출 용도로는 충분하다.
    """
    return induty_code[:2] if induty_code else "??"


def _round_robin_select(buckets: dict[str, list], target: int) -> list:
    """업종 버킷들을 순서대로 하나씩 뽑아 target 개를 채운다 — 특정 업종에 쏠리지 않게."""
    selected = []
    queues = {k: list(v) for k, v in buckets.items()}
    bucket_keys = list(queues.keys())
    while len(selected) < target and any(queues.values()):
        for key in bucket_keys:
            if not queues[key]:
                continue
            selected.append(queues[key].pop(0))
            if len(selected) >= target:
                break
    return selected


def main() -> None:
    fix_console_encoding()
    args = _parse_args()
    out_path = Path(args.out) if args.out else DEFAULT_OUT

    client = DartClient(settings.dart_api_key)

    if args.refresh_cache:
        CORP_CODE_CACHE.unlink(missing_ok=True)
    else:
        _warn_if_corp_code_cache_stale(CORP_CODE_CACHE)

    print("corpCode.xml 로딩 중...")
    entries = client.fetch_corp_codes(cache_path=CORP_CODE_CACHE)
    listed = [e for e in entries if e.stock_code]
    print(f"전체 {len(entries)}건 중 상장(stock_code 보유) {len(listed)}건")

    exclude_path = Path(args.exclude_companies_file) if args.exclude_companies_file else out_path
    excluded_corp_codes = (
        set() if args.no_exclude_existing else _load_excluded_corp_codes(exclude_path)
    )
    if excluded_corp_codes:
        before = len(listed)
        listed = [e for e in listed if e.corp_code not in excluded_corp_codes]
        print(
            f"기존 선정 {len(excluded_corp_codes)}개사 제외({exclude_path.name}) — "
            f"후보 {before}건 -> {len(listed)}건"
        )

    rare_types: set[str] = set()
    if not args.skip_rare_event_scoring:
        post_event_labels_path = (
            Path(args.post_event_labels_file)
            if args.post_event_labels_file
            else DEFAULT_POST_EVENT_LABELS
        )
        rare_types = _rare_outcome_types(post_event_labels_path)
        if rare_types:
            print(f"희귀 사후 이벤트 유형({len(rare_types)}개, 하위 50%): {sorted(rare_types)}")
        else:
            print(
                f"{post_event_labels_path.name} 없음/비어있음 — 희귀 이벤트 가중치 없이 "
                "업종 다양성만으로 선정합니다."
            )

    random.seed(args.seed)
    candidates = random.sample(listed, min(args.candidate_pool, len(listed)))
    print(f"후보 풀 {len(candidates)}건에 company.json 조회 시작 (시장구분+업종코드 확인)...")

    buckets: dict[str, list[dict]] = defaultdict(list)
    checked, kept, failed = 0, 0, 0
    for i, entry in enumerate(candidates, 1):
        try:
            info = client.fetch_company_info(entry.corp_code)
        except (DartApiError, httpx.HTTPError) as exc:
            # Issue #44 — httpx.ConnectError/ReadTimeout 같은 일시적 네트워크 오류를
            # DartApiError 처럼 그 후보만 건너뛰고 계속 간다. 예전엔 이 예외들이 그대로
            # 위로 던져져 main() 전체가 죽었고, out_path.write_text()가 루프 끝에서만
            # 실행돼 이미 조회 완료한 후보들(각각 DART 레이트리밋 예산 소모)이 전부
            # 버려졌다 — 재실행하면 처음부터 다시 조회해야 했다.
            failed += 1
            print(
                f"  [{i}/{len(candidates)}] 실패 {entry.corp_name}: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            continue
        checked += 1
        if info.corp_cls not in TARGET_MARKETS:
            continue
        kept += 1
        rare_score = (
            _rare_event_score(
                client, info.corp_code, rare_types, years=args.rare_event_lookback_years
            )
            if rare_types
            else 0
        )
        buckets[_industry_bucket(info.induty_code)].append(
            {
                "corp_code": info.corp_code,
                "corp_name": info.corp_name,
                "stock_code": info.stock_code,
                "corp_cls": info.corp_cls,
                "induty_code": info.induty_code,
                "_rare_score": rare_score,
            }
        )
        if i % 50 == 0:
            print(f"  진행: {i}/{len(candidates)} 확인, {kept}건 코스피·코스닥 확정")

    print(
        f"\n조회 완료 — 확인 {checked}건, 실패 {failed}건, 코스피·코스닥 {kept}건"
        f"(업종 버킷 {len(buckets)}개)"
    )

    if rare_types:
        # 버킷 안에서 희귀 이벤트를 더 많이 겪은 후보를 먼저 뽑히게 정렬한다 —
        # _round_robin_select()가 각 버킷에서 앞에서부터(pop(0)) 뽑으므로, 여기서
        # 정렬 순서를 정해두면 업종 다양성(버킷 간 라운드로빈)은 그대로 유지하면서
        # 버킷 내부만 "희귀 이벤트 이력 많은 순"으로 우선한다. 동점이면 원래 순서
        # (무작위 후보 추출 순서) 그대로 안정 정렬한다.
        for bucket_key in buckets:
            buckets[bucket_key].sort(key=lambda c: -c["_rare_score"])

    # 버킷 순서를 섞어야 "먼저 나온 업종만 계속 뽑히는" 편향이 안 생긴다
    bucket_items = list(buckets.items())
    random.shuffle(bucket_items)
    selected = _round_robin_select(dict(bucket_items), args.target)
    for c in selected:
        c.pop("_rare_score", None)  # 내부 정렬용 필드 — 저장 결과에는 안 남긴다

    if len(selected) < args.target:
        print(
            f"경고: 목표 {args.target}개 중 {len(selected)}개만 확보됨 — "
            f"--candidate-pool 을 늘려서 재실행하면 더 뽑을 수 있습니다.",
            file=sys.stderr,
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)

    # exclude-companies-file이 out_path와 같으면(기본값) "기존 코퍼스에 신규 회사를
    # 더한다"는 뜻이므로 병합해서 저장한다 — 그렇지 않으면(다른 파일을 가리키면)
    # 이번에 새로 뽑은 것만 저장한다(첫 선정처럼 완전히 새로운 배치를 만드는 경우).
    existing_companies = []
    if exclude_path == out_path and exclude_path.exists():
        existing_companies = json.loads(exclude_path.read_text(encoding="utf-8"))
    final_companies = existing_companies + selected

    out_path.write_text(json.dumps(final_companies, ensure_ascii=False, indent=2), encoding="utf-8")
    if existing_companies:
        new_batch_path = out_path.with_suffix(".new.json")
        new_batch_path.write_text(
            json.dumps(selected, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"이번에 새로 뽑은 {len(selected)}개사만: {new_batch_path}")

    bucket_counts = defaultdict(int)
    for c in selected:
        bucket_counts[_industry_bucket(c["induty_code"])] += 1
    print(f"\n이번 선정 {len(selected)}개사 — 업종 분포({len(bucket_counts)}개 버킷):")
    for bucket, count in sorted(bucket_counts.items(), key=lambda x: -x[1]):
        print(f"  {bucket}: {count}개사")
    print(f"\n저장: {out_path} (전체 {len(final_companies)}개사)")


if __name__ == "__main__":
    main()
