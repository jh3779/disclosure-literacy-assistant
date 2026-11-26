"""회사별 최근 공시 흐름 — 그 회사 자신의 과거 대비 최근 경향(2026-09-17).

"이 회사가 앞으로 뭘 할지"가 아니라 "이 회사가 최근에 어떤 유형의 공시를 더/덜
냈는지"만 다룬다 — 미래를 주장하지 않는다(CLAUDE.md 주의점 1, 등급·신호등·방향성
예측 금지). 같은 회사 안에서 "최근 구간"과 "그 이전 구간"의 form_type 분포를 비교하는
순수 서술 통계라, 다른 회사로 일반화하는 주장이 전혀 없어 통계적으로도 안전하다.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta

from disclosure_lit.dart.ingest import DisclosureSection

DEFAULT_RECENT_MONTHS = 3
# 이 미만이면 "최근 vs 과거" 비교 자체가 통계적으로 무의미하다(표본이 너무 적음).
MIN_TOTAL_DISCLOSURES = 5


@dataclass(frozen=True)
class CompanyTrendItem:
    form_type: str
    recent_count: int
    recent_rate: float  # 최근 구간 공시 중 이 유형의 비율
    baseline_count: int
    baseline_rate: float  # 그 이전 구간 공시 중 이 유형의 비율
    is_increasing: bool  # recent_rate > baseline_rate


def _parse_date(rcept_dt: str) -> date | None:
    if not rcept_dt or len(rcept_dt) < 8:
        return None
    return date(int(rcept_dt[:4]), int(rcept_dt[4:6]), int(rcept_dt[6:8]))


def compute_company_trend(
    corp_code: str,
    sections: list[DisclosureSection],
    *,
    recent_months: int = DEFAULT_RECENT_MONTHS,
) -> list[CompanyTrendItem]:
    """그 회사의 공시(rcept_no 단위 중복 제거)를 form_type별로 "최근 N개월" vs
    "그 이전 전체 이력"으로 나눠 비율을 비교한다.

    공시가 너무 적거나(전체 5건 미만), 최근/과거 어느 한쪽이 비어 있으면(비교 불가)
    빈 리스트를 반환한다 — 에러가 아니라 "비교할 근거가 부족하다"는 뜻이다.
    """
    own_sections = [s for s in sections if s.corp_code == corp_code]
    seen_rcept: dict[str, tuple[date, str]] = {}
    for section in own_sections:
        parsed = _parse_date(section.rcept_dt)
        if parsed is None:
            continue
        seen_rcept.setdefault(section.rcept_no, (parsed, section.form_type))

    if len(seen_rcept) < MIN_TOTAL_DISCLOSURES:
        return []

    max_date = max(d for d, _ in seen_rcept.values())
    cutoff = max_date - timedelta(days=recent_months * 30)

    recent_counts: Counter[str] = Counter()
    baseline_counts: Counter[str] = Counter()
    for disclosure_date, form_type in seen_rcept.values():
        if disclosure_date > cutoff:
            recent_counts[form_type] += 1
        else:
            baseline_counts[form_type] += 1

    recent_total = sum(recent_counts.values())
    baseline_total = sum(baseline_counts.values())
    if recent_total == 0 or baseline_total == 0:
        return []

    all_form_types = set(recent_counts) | set(baseline_counts)
    items = [
        CompanyTrendItem(
            form_type=form_type,
            recent_count=recent_counts.get(form_type, 0),
            recent_rate=recent_counts.get(form_type, 0) / recent_total,
            baseline_count=baseline_counts.get(form_type, 0),
            baseline_rate=baseline_counts.get(form_type, 0) / baseline_total,
            is_increasing=(recent_counts.get(form_type, 0) / recent_total)
            > (baseline_counts.get(form_type, 0) / baseline_total),
        )
        for form_type in all_form_types
    ]
    return sorted(items, key=lambda item: -(item.recent_rate - item.baseline_rate))
