"""A11 — 상투구(IR 화법) 후보 문장 추출. 담당: A(이승노).

A10(select_corpus_companies.py + build_corpus.py)이 모은 data/raw/<corp_code>/sections.jsonl
전체에서 "여러 회사가 공통으로 쓰는" 문장 템플릿을 후보로 뽑는다.

담당 범위 주의: 실제 임베딩 클러스터링(dictionary/cluster.py::cluster_phrases)과 사후 이벤트
통계(dictionary/stats.py::compute_stats)는 C(우지훈) 담당이다(두 파일 docstring에 명시,
2026-09-11 DEC-007). 이 스크립트는 그 입력이 될 "후보 문장 목록"만 만든다 —
docs/proposals/finance-pivot/02-disclosure-dictionary.md 10~11절이 A 담당 범위를 "DART 공시
코퍼스 구축, 상투구 후보 추출"로, Phase 1 완료 기준을 "후보 문장 목록 확보"로 명시한다.

접근:
  같은 회사가 자기 공시에서 같은 문장을 반복하는 건 그 회사의 관행일 뿐이지만, 서로 무관한
  여러 회사가 똑같은(또는 숫자·날짜만 다른) 문장을 쓴다면 업계 표준 IR 화법(상투구)일
  가능성이 높다 — "서로 다른 회사에 걸쳐 등장한 횟수"를 후보 판별의 1차 기준으로 쓴다.
  최종 의미 클러스터링(비슷하지만 완전히 같은 문장은 아닌 것들을 묶는 것)은 임베딩이 필요해
  C의 몫이고, 여기서는 정규화 후 문자열이 정확히 같은 것만 묶는 보수적인 1차 후보 추출이다.

  1. 섹션 텍스트를 문장 단위로 분리(마침표·물음표·느낌표 기준, 소수점은 경계로 안 봄)
  2. 숫자·날짜를 플레이스홀더(#)로 정규화 — "2025-08-26"과 "2025-07-25"처럼 숫자만 다른
     문장을 같은 후보로 묶기 위함
  3. 정규화된 문장별로 등장한 서로 다른 corp_code 수를 센다
  4. --min-companies(기본 5) 이상인 문장만 후보로 채택
  5. 표 데이터·숫자 나열처럼 화법이 아닌 잡음은 최소 길이 + 숫자 비율 필터로 거른다

실행:
    uv run python scripts/extract_cliche_candidates.py
    uv run python scripts/extract_cliche_candidates.py --min-companies 3 --raw-dir data/raw
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

# collect_disclosures.py 와 같은 디렉터리 스크립트를 모듈처럼 임포트
from collect_disclosures import fix_console_encoding

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RAW_DIR = PROJECT_ROOT / "data" / "raw"
DEFAULT_OUT = PROJECT_ROOT / "data" / "raw" / "cliche_candidates.jsonl"

# 문장 분리: 마침표/물음표/느낌표 뒤에 공백이 오면 경계로 본다. 숫자 사이의 마침표(소수점,
# "1,234.5" 등)는 앞뒤가 숫자면 제외 — chunk_disclosure 가 태그를 벗기면서 공백으로 이어붙인
# 표 데이터에서 소수점을 문장 경계로 오인하지 않기 위함.
_SENTENCE_SPLIT = re.compile(r"(?<!\d)(?<=[.?!])\s+(?!\d)")

# 정규화: 숫자(콤마·소수점·하이픈 포함 연속 구간)를 플레이스홀더 하나로 치환.
_NUMBER_RUN_RE = re.compile(r"[0-9][0-9,.\-:]*")
_WS_RE = re.compile(r"\s+")

MIN_SENTENCE_LEN = 12  # 너무 짧은 조각(표 헤더·단답형 필드)은 화법 후보로 보기 어렵다
MAX_DIGIT_RATIO = 0.3  # 숫자 비율이 높으면 화법 문장이 아니라 표 데이터일 가능성이 크다

# 정기공시(사업보고서 등)는 "가. 나. 다.", "1. 2. 3.", "7-1.", "X.", "Ⅲ.", "IX." 식 순번
# 마커로 항목을 나눈다. 원문에서 개행이던 자리가 chunk_disclosure 의 공백 정규화로 사라지면서
# "...관한 사항\n가. 회사는"이 "...관한 사항 가. 회사는"으로 붙고, 마침표 기준 문장 분리가 그
# 사이를 갈라 "...관한 사항 가."/"...관한 사항 7-1."라는 header+marker 잔재를 독립된
# "문장"으로 만들어버린다(실측 확인 — 한글 마커·숫자 마커·"7-1" 식 하위번호 마커·유니코드
# 로마숫자(Ⅲ,Ⅵ...)·아스키 로마숫자(IX 등) 순서로 드러남, 전부 상위 후보를 오염시켰다) —
# 화법이 아니라 목차 구조라 걸러야 한다.
_ASCII_ROMAN_NUMERAL = re.compile(
    r"^(?=[IVXLCDM])M{0,4}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})$"
)
_LIST_MARKER = re.compile(r"^([가-힣]|[Ⅰ-Ⅻ]|[\d\-.:]+)$")


def _is_list_marker_token(token: str) -> bool:
    """단독으로 목차 순번 마커일 뿐인 어절인지 판단한다(_LIST_MARKER 설명 참고).

    아스키 로마숫자(IX, XI 등)는 단순히 "라틴 알파벳으로만 된 어절"로 뭉뚱그리면 진짜
    영문 단어까지 걸러낼 위험이 있어(예: "IR"), 로마숫자 문법에 맞는지 별도로 검증한다
    (`_ASCII_ROMAN_NUMERAL`) — I·V·X·L·C·D·M 외 다른 알파벳이 섞이면 매치되지 않는다.
    """
    return bool(_LIST_MARKER.match(token)) or bool(_ASCII_ROMAN_NUMERAL.match(token))


# "【 대표이사 등의 확인 】"류 전각 괄호는 DART 섹션 제목에만 쓰이는 관례적 표기라 화법일 수
# 없다(retrieval/company_index.py 검색 결과 예시에서도 동일 패턴 관측).
_SECTION_TITLE_BRACKETS = re.compile(r"[【】]")

# K-IFRS 회계기준 주석 어휘 — form_type 필터(EXCLUDED_FORM_TYPES)로 "정기공시"를 뺐는데도
# 상위 후보가 여전히 회계 주석으로 뒤덮였다(실측, 25개사 드라이런): 주주총회_소집·
# 의결권대리행사권유·증권발행_신고는 법적으로 재무제표 전문을 첨부해야 하는 공시라, 그
# 첨부문서 안의 회계기준 주석이 그대로 섹션으로 청킹된다. form_type을 계속 추가하는 건
# 두더지잡기라 — "기타"처럼 진짜 화법도 섞인 잡다 카테고리는 통째로 뺄 수 없다 — 내용
# 자체로 판별한다.
#
# 두 단계로 나눈다: _KIFRS_STRONG_MARKERS 는 회계기준 문서가 아니면 거의 쓰이지 않는
# 표현이라 1회 등장만으로 제외하고, _KIFRS_WEAK_VOCAB 는 문맥에 따라 일반 서술에도 나올 수
# 있어(예: "공정가치") 2개 이상 겹칠 때만 제외한다.
_KIFRS_STRONG_MARKERS = frozenset(
    {
        "기준서",
        "조기적용",
        "회계정책",
        "리스이용자",
        "리스제공자",
        "사용권자산",
        "상각후원가",
        "유효이자율법",
    }
)
_KIFRS_WEAK_VOCAB = frozenset(
    {
        "금융자산",
        "금융부채",
        "기타포괄손익",
        "공정가치",
        "당기손익",
        "연결재무제표",
        "지분상품",
    }
)


def _split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT.split(text) if s.strip()]


def normalize_sentence(sentence: str) -> str:
    """숫자·날짜를 플레이스홀더로 치환해 "숫자만 다른 같은 문장"을 하나의 후보로 묶는다."""
    normalized = _NUMBER_RUN_RE.sub("#", sentence)
    return _WS_RE.sub(" ", normalized).strip()


def is_candidate_sentence(sentence: str) -> bool:
    """화법 후보로 볼 만한 최소 조건.

    - 너무 짧으면(표 헤더·단답형 필드) 제외
    - 마침표/물음표/느낌표로 끝나지 않으면 제외 — 문장 분리기가 만든 마지막 잔여 조각(제목만
      있고 서술어가 없는 텍스트)은 화법이 아니다
    - 숫자 비율이 높으면 표 데이터로 보고 제외
    - 마지막 어절이 마침표를 뗀 뒤 목차 순번 마커뿐이면 제외 — "...관한 사항 가."/
      "...관한 사항 1."처럼 정기공시 순번 마커가 앞 헤더에 들러붙은 잔재다
      (_is_list_marker_token 설명)
    - 전각 괄호(【 】)가 있으면 제외 — DART 섹션 제목 표기 관례라 화법일 수 없다
    - K-IFRS 회계기준 특유 표현(_KIFRS_STRONG_MARKERS)이 하나라도 있으면 제외, 문맥에 따라
      일반 서술에도 나올 수 있는 어휘(_KIFRS_WEAK_VOCAB)는 2개 이상 겹칠 때만 제외 — 회계기준
      원문 인용일 가능성이 높다(_KIFRS_STRONG_MARKERS/_KIFRS_WEAK_VOCAB 설명 참고)
    """
    if len(sentence) < MIN_SENTENCE_LEN:
        return False
    if sentence[-1] not in ".?!":
        return False
    digit_count = sum(1 for ch in sentence if ch.isdigit())
    if digit_count / len(sentence) > MAX_DIGIT_RATIO:
        return False
    if _SECTION_TITLE_BRACKETS.search(sentence):
        return False
    words = sentence.rstrip(".?!").split()
    if words and _is_list_marker_token(words[-1]):
        return False
    if any(term in sentence for term in _KIFRS_STRONG_MARKERS):
        return False
    if sum(1 for term in _KIFRS_WEAK_VOCAB if term in sentence) >= 2:
        return False
    return True


# 정기공시(사업보고서·분기·반기보고서)는 K-IFRS 회계기준 원문을 그대로 베낀 법정 의무
# 주석 문구가 대부분이다(예: "해당 기준서의 개정이 재무제표에 미치는 중요한 영향은
# 없습니다") — 회사가 선택하는 화법이 아니라 거의 모든 회사가 100%에 가깝게 반복하는
# 규제 문구라, 등장 자체가 사후 이벤트와 통계적으로 아무 관계가 없다(항상 등장하니까
# 상관관계가 안 나온다). 기획서가 예로 든 "선제적 재무구조 개선" 같은 진짜 화법은
# MOU·유상증자·자율공시 같은 재량적 서술에서 나온다 — 실측(20개사 드라이런)으로 확인:
# 정기공시를 포함하면 상위 후보 전부가 회계 주석으로 뒤덮였다.
EXCLUDED_FORM_TYPES = frozenset({"정기공시"})


@dataclass
class ClicheCandidate:
    normalized: str
    distinct_company_count: int
    total_occurrences: int
    form_types: list[str]
    samples: list[dict[str, str]]  # [{corp_code, rcept_no, text}, ...]


def _iter_sections(raw_dir: Path):
    for sections_path in sorted(raw_dir.glob("*/sections.jsonl")):
        with sections_path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                yield json.loads(line)


def extract_candidates(
    sections: list[dict],
    *,
    min_companies: int = 5,
    max_samples: int = 3,
    excluded_form_types: frozenset[str] = EXCLUDED_FORM_TYPES,
    max_company_fraction: float = 0.5,
) -> list[ClicheCandidate]:
    """섹션 목록에서 회사 간 반복 문장 후보를 뽑는다. 회사당 중복 등장은 1회로만 센다.

    같은 회사가 자기 공시 안에서 같은 문장을 여러 번 반복해도(예: MOU 표준 서두 문구)
    `distinct_company_count`(핵심 판별 기준)에는 1로만 잡힌다 — 그 회사의 반복 습관이
    "여러 회사가 공유하는 화법"으로 부풀려지지 않게 하기 위함. `total_occurrences`는
    참고용으로 전체 등장 횟수를 따로 유지한다.

    `excluded_form_types`(기본 EXCLUDED_FORM_TYPES)에 속한 섹션은 통째로 건너뛴다 — 법정
    의무 문구가 화법 후보를 오염시키는 걸 막기 위함(EXCLUDED_FORM_TYPES 주석 참고).

    `max_company_fraction`(기본 0.5): 전체 회사 수 대비 이 비율을 넘는 회사에서 등장한
    문장은 제외한다. is_candidate_sentence 의 K-IFRS 어휘 필터(_KIFRS_STRONG_MARKERS/
    _KIFRS_WEAK_VOCAB)로 못 잡는 회계기준 주석이 실측(26개사 드라이런)에서도 계속
    나왔다 — 리스·손상·수익인식 등 기준서마다 어휘가 달라 끝없이 단어를 추가하는 건
    두더지잡기다. 반면 이런 법정 의무 문구의 공통된 성질은 "회사가 선택하는 게 아니라
    전체가 거의 다 쓴다"는 것이므로, 회사 수 비율 자체를 상한으로 걸어 어휘 목록을
    영원히 늘리지 않고도 일반화되게 걸러낸다 — 진짜 화법(cliché)은 일부 회사만 선택적으로
    쓰기 때문에 이 상한을 넘지 않을 것으로 기대한다. 전체 회사 수가 작을 때는(예: 드라이런)
    상한이 낮게 걸려 유의미한 후보까지 걸러질 수 있다 — 최종 100개사 규모에서 재검증 필요.
    """
    buckets: dict[str, dict] = defaultdict(
        lambda: {"companies": set(), "count": 0, "form_types": set(), "samples": []}
    )
    total_companies = len({s["corp_code"] for s in sections})
    max_companies = total_companies * max_company_fraction

    for section in sections:
        form_type = section.get("form_type", "")
        if form_type in excluded_form_types:
            continue
        corp_code = section["corp_code"]
        for sentence in _split_sentences(section.get("text", "")):
            if not is_candidate_sentence(sentence):
                continue
            key = normalize_sentence(sentence)
            if not key:
                continue
            bucket = buckets[key]
            bucket["companies"].add(corp_code)
            bucket["count"] += 1
            bucket["form_types"].add(form_type)
            if len(bucket["samples"]) < max_samples:
                bucket["samples"].append(
                    {
                        "corp_code": corp_code,
                        "rcept_no": section.get("rcept_no", ""),
                        "text": sentence,
                    }
                )

    candidates = [
        ClicheCandidate(
            normalized=key,
            distinct_company_count=len(bucket["companies"]),
            total_occurrences=bucket["count"],
            form_types=sorted(bucket["form_types"]),
            samples=bucket["samples"],
        )
        for key, bucket in buckets.items()
        if min_companies <= len(bucket["companies"]) <= max_companies
    ]
    candidates.sort(key=lambda c: (-c.distinct_company_count, -c.total_occurrences))
    return candidates


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--raw-dir", default=None, help="수집 데이터 루트(기본 data/raw)")
    parser.add_argument(
        "--min-companies", type=int, default=5, help="후보로 채택할 최소 서로 다른 회사 수(기본 5)"
    )
    parser.add_argument(
        "--max-company-fraction",
        type=float,
        default=0.5,
        help="전체 회사 수 대비 이 비율을 넘는 회사에서 등장하면 제외(기본 0.5) — 법정 의무 "
        "문구처럼 회사가 선택하는 게 아니라 거의 전부가 쓰는 문장을 거르기 위함",
    )
    parser.add_argument(
        "--out", default=None, help="결과 저장 경로(기본 data/raw/cliche_candidates.jsonl)"
    )
    return parser.parse_args()


def main() -> None:
    fix_console_encoding()
    args = _parse_args()
    raw_dir = Path(args.raw_dir) if args.raw_dir else DEFAULT_RAW_DIR
    out_path = Path(args.out) if args.out else DEFAULT_OUT

    print(f"{raw_dir} 이하 sections.jsonl 스캔 중...")
    sections = list(_iter_sections(raw_dir))
    companies = {s["corp_code"] for s in sections}
    excluded_count = sum(1 for s in sections if s.get("form_type", "") in EXCLUDED_FORM_TYPES)
    print(
        f"섹션 {len(sections)}건, 회사 {len(companies)}개사 로딩 완료 "
        f"(그중 {excluded_count}건은 {sorted(EXCLUDED_FORM_TYPES)} 제외 대상)"
    )

    candidates = extract_candidates(
        sections, min_companies=args.min_companies, max_company_fraction=args.max_company_fraction
    )
    print(f"후보 {len(candidates)}건 (최소 {args.min_companies}개 회사 이상 등장)")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for c in candidates:
            f.write(
                json.dumps(
                    {
                        "normalized": c.normalized,
                        "distinct_company_count": c.distinct_company_count,
                        "total_occurrences": c.total_occurrences,
                        "form_types": c.form_types,
                        "samples": c.samples,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    print(f"저장: {out_path}")

    if candidates:
        print("\n상위 10건 (회사 수 기준):")
        for c in candidates[:10]:
            preview = c.normalized[:60] + ("..." if len(c.normalized) > 60 else "")
            print(f"  [{c.distinct_company_count}개사, {c.total_occurrences}회] {preview}")


if __name__ == "__main__":
    main()
