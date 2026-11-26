"""normalize_form_type 미분류("기타") 비율 측정 — A5 검증용, 회귀 감시용 둘 다.

제목 매칭은 정확한 분류기가 아니라 보수적인 1차 라벨이라, 매핑 테이블이 실제 시장 공시를
얼마나 커버하는지 주기적으로 확인해야 한다. list.json 을 직접 호출한다(다른 A 작업
브랜치의 병합 여부와 무관하게 독립적으로 돌아가도록 disclosure_lit.dart.client 에는
의존하지 않는다).

실행:
    uv run python scripts/form_type_coverage.py
"""

from __future__ import annotations

from collections import Counter
from datetime import date, timedelta

import httpx

from disclosure_lit.config import settings
from disclosure_lit.dart.ingest import FALLBACK_FORM_TYPE, normalize_form_type

LIST_URL = "https://opendart.fss.or.kr/api/list.json"
PAGES_PER_MARKET = 5  # 시장당 최대 5 * 100 = 500건


def _fetch_report_names(corp_cls: str, *, bgn_de: str, end_de: str) -> list[str]:
    names: list[str] = []
    for page_no in range(1, PAGES_PER_MARKET + 1):
        resp = httpx.get(
            LIST_URL,
            params={
                "crtfc_key": settings.dart_api_key,
                "corp_cls": corp_cls,
                "bgn_de": bgn_de,
                "end_de": end_de,
                "page_no": str(page_no),
                "page_count": "100",
            },
            timeout=10.0,
        )
        resp.raise_for_status()
        body = resp.json()
        if body.get("status") == "013":
            break
        names.extend(row["report_nm"] for row in body.get("list", []))
        if page_no >= int(body.get("total_page") or 1):
            break
    return names


def main() -> None:
    end = date.today()
    bgn = end - timedelta(days=14)
    names: list[str] = []
    bgn_de, end_de = bgn.strftime("%Y%m%d"), end.strftime("%Y%m%d")
    for corp_cls, label in (("Y", "코스피"), ("K", "코스닥")):
        fetched = _fetch_report_names(corp_cls, bgn_de=bgn_de, end_de=end_de)
        print(f"{label}: {len(fetched)}건 수집")
        names.extend(fetched)

    counts = Counter(normalize_form_type(name) for name in names)
    total = sum(counts.values())
    unmatched = counts.get(FALLBACK_FORM_TYPE, 0)

    print(f"\n총 {total}건 중 카테고리별 분포:")
    for form_type, n in counts.most_common():
        print(f"  {form_type:24s} {n:5d}  ({n / total:.1%})")

    print(f"\n미분류('{FALLBACK_FORM_TYPE}') 비율: {unmatched}/{total} ({unmatched / total:.1%})")

    print("\n미분류 제목 샘플(최대 15개):")
    unmatched_titles = [n for n in names if normalize_form_type(n) == FALLBACK_FORM_TYPE]
    for title in sorted(set(unmatched_titles))[:15]:
        print(f"  {title.strip()}")


if __name__ == "__main__":
    main()
