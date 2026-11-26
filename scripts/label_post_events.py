"""A12 — 사후 이벤트 라벨링. 담당: A(이승노).

A10이 모은 100개사 공시 이력에서, 공시 하나마다 "그 이후 N개월 내 같은 회사에 또 어떤
공시가 있었는지"를 라벨링한다. C의 `dictionary/stats.py::compute_stats()`가 화법
클러스터별로 "N건 중 M건이 유상증자로 이어짐" 통계를 계산하려면 이 원재료(공시별 사후
이벤트 라벨)가 있어야 한다 — 클러스터 소속(C 담당, `cluster_phrases()`)과 이 라벨을
rcept_no 로 조인하면 클러스터 단위 집계가 가능해진다.

A10이 수집 종료일을 "오늘 - 6개월"로 잡아둔 게 이 라벨링을 위한 관찰 여유였다
(scripts/build_corpus.py 참고) — 그래서 지금 시점에 바로 라벨링이 가능하다.

담당 범위 주의: 클러스터별 집계(compute_stats)는 C 담당이다. 이 스크립트는 "공시
하나 → 그 이후 N개월 내 관측된 이벤트 목록"까지만 만든다.

실행:
    uv run python scripts/label_post_events.py
    uv run python scripts/label_post_events.py --lookback-months 6
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

# collect_disclosures.py 와 같은 디렉터리 스크립트를 모듈처럼 임포트
from collect_disclosures import fix_console_encoding

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RAW_DIR = PROJECT_ROOT / "data" / "raw"
DEFAULT_OUT = PROJECT_ROOT / "data" / "raw" / "post_event_labels.jsonl"

# 기획서 예시("이 표현 이후 3개월 내 9건이 유상증자로 이어짐")와 일치시켰다. A10이 수집
# 종료일을 "오늘-6개월"로 잡아둔 건 이 관찰 기간에 여유를 두기 위함이라, 3개월은 그
# 여유(6개월) 안에 안전하게 들어간다 — 수집 경계에 가까운 공시도 대부분 온전한 사후
# 관찰 기간을 확보한다(아래 "우측 절단" 설명 참고).
LOOKBACK_MONTHS_DEFAULT = 3

# 어떤 form_type을 "사후 이벤트"로 볼지 — normalize_form_type()이 이미 분류해둔 라벨 중
# 자본구조·지배구조·시장상태를 바꾸는 굵직한 기업 행동만 추린다. 정기공시·지분보고·
# 주주총회 소집 통지처럼 일상적/절차적인 공시는 "사후 이벤트"라기보다 "늘 일어나는 일"이라
# 뺐다 — 화법이 예측할 만한 대상이 아니다(항상 관측되면 통계적으로 무의미해지는 것과 같은
# 논리로, A11 extract_cliche_candidates.py 의 EXCLUDED_FORM_TYPES/max_company_fraction과
# 같은 원칙).
OUTCOME_FORM_TYPES = frozenset(
    {
        "유상증자",
        "무상증자",
        "감자",
        "자기주식_취득",
        "자기주식_처분",
        "최대주주_변경",
        "전환사채_발행",
        "전환사채_조기취득",
        "전환사채_매도",
        "신주인수권부사채_발행",
        "타법인출자_취득",
        "타법인출자_처분",
        "채무보증",
        "주식소각",
        "회사합병",
        "거래정지",
        "거래재개",
        "제재_벌금",
        "영업정지",
        "공급계약_체결",
        "공급계약_해지",
    }
)


@dataclass
class PostEventLabel:
    rcept_no: str
    corp_code: str
    rcept_dt: str
    form_type: str
    outcome_events: list[str]  # 관찰 기간 내 등장한 서로 다른 사후 이벤트 form_type
    outcome_count: int  # 사후 이벤트로 분류된 공시 총 건수(같은 form_type 중복 포함)
    is_censored: bool  # 관찰 기간이 수집 경계를 넘어가 "이벤트 없음"을 확신할 수 없음


def _parse_date(rcept_dt: str) -> date:
    return date(int(rcept_dt[:4]), int(rcept_dt[4:6]), int(rcept_dt[6:8]))


def _iter_disclosures(raw_dir: Path):
    """sections.jsonl 전체에서 공시(rcept_no) 단위로 중복 제거해 하나씩 낸다.

    섹션은 공시 하나당 여러 개일 수 있지만 rcept_dt/form_type은 공시 단위로 동일하므로
    처음 만난 섹션의 값을 그 공시의 대표값으로 쓴다.
    """
    seen: set[tuple[str, str]] = set()
    for sections_path in sorted(raw_dir.glob("*/sections.jsonl")):
        with sections_path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)
                key = (d["corp_code"], d["rcept_no"])
                if key in seen or not d.get("rcept_dt"):
                    continue
                seen.add(key)
                yield d


def label_post_events(
    disclosures: list[dict], *, lookback_months: int = LOOKBACK_MONTHS_DEFAULT
) -> list[PostEventLabel]:
    """공시마다 "그 이후 lookback_months개월 내" 같은 회사의 사후 이벤트를 라벨링한다.

    같은 회사의 공시를 날짜순으로 정렬한 뒤 투 포인터로 관찰 기간 안의 공시를 찾는다
    (회사별 O(n) — 전체 코퍼스 규모에서도 충분히 빠르다).

    `is_censored`: 관찰 기간의 끝이 코퍼스에 실제로 수집된 가장 최근 날짜를 넘어가면
    True다 — "그 기간에 이벤트가 없었다"가 아니라 "그 기간을 아직 다 못 봤다"는 뜻이라
    (우측 절단, right-censoring), `outcome_count == 0`과 구분해야 한다. 안 그러면
    수집 경계에 가까운 최근 공시들이 전부 "이벤트 없음"으로 잘못 집계되어 사후 통계가
    왜곡된다.
    """
    by_company: dict[str, list[dict]] = defaultdict(list)
    for d in disclosures:
        by_company[d["corp_code"]].append(d)

    corpus_max_date = max(_parse_date(d["rcept_dt"]) for d in disclosures)

    labels: list[PostEventLabel] = []
    for corp_code, items in by_company.items():
        items = sorted(items, key=lambda d: d["rcept_dt"])
        dates = [_parse_date(d["rcept_dt"]) for d in items]
        n = len(items)

        end_idx = 0
        for i in range(n):
            window_end = dates[i] + timedelta(days=lookback_months * 30)
            if end_idx < i + 1:
                end_idx = i + 1
            while end_idx < n and dates[end_idx] <= window_end:
                end_idx += 1

            window_items = items[i + 1 : end_idx]
            outcome_form_types = [
                w["form_type"] for w in window_items if w["form_type"] in OUTCOME_FORM_TYPES
            ]
            labels.append(
                PostEventLabel(
                    rcept_no=items[i]["rcept_no"],
                    corp_code=corp_code,
                    rcept_dt=items[i]["rcept_dt"],
                    form_type=items[i]["form_type"],
                    outcome_events=sorted(set(outcome_form_types)),
                    outcome_count=len(outcome_form_types),
                    is_censored=window_end > corpus_max_date,
                )
            )
    return labels


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--raw-dir", default=None, help="수집 데이터 루트(기본 data/raw)")
    parser.add_argument(
        "--lookback-months",
        type=int,
        default=LOOKBACK_MONTHS_DEFAULT,
        help=f"사후 관찰 기간(개월, 기본 {LOOKBACK_MONTHS_DEFAULT})",
    )
    parser.add_argument(
        "--out", default=None, help="결과 저장 경로(기본 data/raw/post_event_labels.jsonl)"
    )
    return parser.parse_args()


def main() -> None:
    fix_console_encoding()
    args = _parse_args()
    raw_dir = Path(args.raw_dir) if args.raw_dir else DEFAULT_RAW_DIR
    out_path = Path(args.out) if args.out else DEFAULT_OUT

    print(f"{raw_dir} 이하 sections.jsonl 스캔 중...")
    disclosures = list(_iter_disclosures(raw_dir))
    companies = {d["corp_code"] for d in disclosures}
    print(f"공시 {len(disclosures)}건, 회사 {len(companies)}개사 로딩 완료")

    labels = label_post_events(disclosures, lookback_months=args.lookback_months)
    print(f"라벨 {len(labels)}건 생성 (관찰 기간 {args.lookback_months}개월)")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for label in labels:
            f.write(
                json.dumps(
                    {
                        "rcept_no": label.rcept_no,
                        "corp_code": label.corp_code,
                        "rcept_dt": label.rcept_dt,
                        "form_type": label.form_type,
                        "outcome_events": label.outcome_events,
                        "outcome_count": label.outcome_count,
                        "is_censored": label.is_censored,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    print(f"저장: {out_path}")

    usable = [label for label in labels if not label.is_censored]
    censored = len(labels) - len(usable)
    print(f"\n관찰 기간이 수집 경계를 넘어 절단된 라벨: {censored}건 (통계에서 제외 권장)")
    if usable:
        with_outcome = sum(1 for label in usable if label.outcome_count > 0)
        print(
            f"판정 가능한 라벨 {len(usable)}건 중 사후 이벤트 있음: {with_outcome}건 "
            f"({with_outcome / len(usable) * 100:.1f}%)"
        )


if __name__ == "__main__":
    main()
