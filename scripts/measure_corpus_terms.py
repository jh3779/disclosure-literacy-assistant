"""로컬 코퍼스에서 특정 표현이 몇 번 나오는지 센다 — 투자 조언 필터의 경계 근거.

**왜 필요한가**: `factcheck/compliance.py`의 필터는 "원문에 0회 나오는 조언 발화만
막는다"는 기준(DEC-034/036)으로 경계를 긋는다. 그 기준을 지키려면 패턴을 넓힐 때마다
실제 코퍼스에서 0회임을 확인해야 하는데, 지금까지는 일회성 스크립트로 재고 결과만
문서에 적어 와서 **다른 사람이 같은 숫자를 재현할 수 없었다**(2026-09-24 자체 리뷰 지적:
같은 코퍼스 크기에서 일부 수치가 재현되지 않음).

사용법:

    uv run python scripts/measure_corpus_terms.py 매수의견 매도의견 "매수 등급"
    uv run python scripts/measure_corpus_terms.py --raw-dir /path/to/data/raw 투자의견

출력은 표현별 (총 등장 횟수, 포함 섹션 수)다. **총 등장 횟수**는 같은 섹션 안 중복까지
세고, **섹션 수**는 그 표현이 하나라도 있는 섹션을 센다 — 문서에 숫자를 적을 땐 어느
쪽인지 함께 밝힐 것(이 둘을 섞어 적은 것이 과거 재현 실패의 원인으로 추정된다).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from disclosure_lit.config import PROJECT_ROOT


def count_terms(raw_dir: Path, terms: list[str]) -> tuple[int, dict[str, tuple[int, int]]]:
    """(섹션 총수, {표현: (총 등장 횟수, 포함 섹션 수)})를 돌려준다."""
    totals = dict.fromkeys(terms, 0)
    sections_with = dict.fromkeys(terms, 0)
    n_sections = 0

    for path in sorted(raw_dir.glob("*/sections.jsonl")):
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                text = json.loads(line).get("text", "")
                n_sections += 1
                for term in terms:
                    hits = text.count(term)
                    if hits:
                        totals[term] += hits
                        sections_with[term] += 1

    return n_sections, {t: (totals[t], sections_with[t]) for t in terms}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("terms", nargs="+", help="세고 싶은 표현들")
    parser.add_argument("--raw-dir", type=Path, default=PROJECT_ROOT / "data" / "raw")
    args = parser.parse_args()

    n_sections, counts = count_terms(args.raw_dir, args.terms)
    print(f"코퍼스: {args.raw_dir} · 섹션 {n_sections:,}건")
    print(f"{'표현':<24}{'총 등장':>10}{'포함 섹션':>12}")
    for term, (total, n_with) in counts.items():
        print(f"{term:<24}{total:>10,}{n_with:>12,}")


if __name__ == "__main__":
    main()
