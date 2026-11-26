"""종목별 공시 이력 검색(키워드+임베딩 하이브리드) — L1.

담당: A(이승노) 인덱싱, C(우지훈) 조회 연동.

**2026-09-18 FAISS 도입**: 회사당 섹션 수가 지금 규모(코퍼스 82,876섹션/114개사,
회사당 수백~수천)에서는 numpy 브루트포스로도 충분히 빠르지만, 팀 결정으로 미리
`faiss.IndexFlatIP`(정확 검색, 근사 아님)를 도입한다 — 기존 numpy 구현과 수치적으로
동일한 결과를 내도록 배선했다(`phrase_index.py`의 "2026-09-18 FAISS 도입" 참고,
같은 이유·같은 패턴). 하이브리드 스코어(코사인+키워드 보너스-거대섹션 감점)를
정확히 재현하려면 top_k만 뽑는 근사 검색이 아니라 **회사 내 전체 후보의 정확한
코사인 유사도**가 필요해서, `IndexFlatIP`로 전체를 조회한 뒤 원래 순서로 재배열해
기존 스코어링 로직에 그대로 넘긴다.
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass

import faiss
import numpy as np

from disclosure_lit.dart.ingest import DisclosureSection
from disclosure_lit.embed import embed_query, embed_texts


@dataclass(frozen=True)
class SearchHit:
    section: DisclosureSection
    score: float | None  # 검색어 없는 최신순 조회에는 관련성 점수가 없다.


# 코사인 유사도 하한 — 실측(dragonkue/BGE-m3-ko, "유상증자" 쿼리 기준):
#   완전 무관("오늘 날씨가 맑습니다")            ~0.16
#   같은 도메인이지만 무관("자기주식 취득...")     ~0.27
#   의미적으로 관련(키워드 없이 "신주 발행" 언급)  ~0.33
#   키워드 그대로 포함                          ~0.52+
# 0.3 을 기준으로 잡으면 "같은 도메인 무관"은 걸러지고 "의미적 관련"은 통과한다.
# 키워드가 부분 문자열로 정확히 매치되면 +1.0 보너스를 더해 항상 이 문턱을 넘게 한다
# (기존 순수 키워드 검색이 보장하던 "정확히 일치하면 반드시 반환" 동작을 유지).
#
# 2026-09-21(이슈 #105, @9abin 실측): 다단어 검색어는 이 보너스가 사실상 무력화됐다 —
# needle 전체를 문장 그대로 substring 매칭하는데, 실제 공시 텍스트는 "전환사채(해외
# 전환사채 포함) 발행후..."처럼 단어 사이에 다른 문구가 끼어 있어 "전환사채 발행"이라는
# 연속 문자열이 그대로 나오는 경우가 드물다. 그러면 keyword_bonus가 항상 0이 되고
# 순위가 순수 코사인 유사도로만 결정되는데, 흔한 단어("발행")의 유사도가 희귀하고
# 구체적인 단어("전환사채")의 신호를 덮어버려 완전히 무관한 섹션이 상위로 올라왔다
# (실측: "구영테크 전환사채 발행" 검색 시 top5 전부 "I. 회사의 개요"). 그래서 `_keyword_
# bonus_for()`가 전체 문자열이 안 맞으면 공백 기준 단어 단위로 쪼개 겹치는 비율만큼
# 부분 보너스를 준다.
#
# R106-1/R106-2 (2026-09-21 외부 리뷰) 수정: 위 1차 구현은 두 가지 구멍이 있었다.
# (1) 흩어진 단어가 전부(100%) 겹치면 정확 구문과 똑같이 만점(1.0)을 받아, "정확히
# 일치하면 우선한다"는 기존 보장이 점수에 반영되지 않았다("유상증자 결정을
# 공시합니다" vs "유상증자는 아니고 결정된 바 없음"이 동점). (2) 겹치는 단어가
# 검색어의 절반 이하(예: 2단어 중 1개, "전환사채 발행" 검색에 "발행"만)여도 부분
# 점수가 붙어, 핵심어 없이 흔한 단어 하나만으로 관련성 문턱(min_score)을 넘는
# 경로가 생겼다. 그래서 부분 일치는 (a) 과반(절반 초과)을 넘겨야만 점수를 받고
# (b) 상한을 정확 구문(1.0)보다 확실히 낮은 값으로 캡한다 — 이슈 #105가 고치려던
# "흩어진 다단어도 어느 정도는 반영" 자체는 유지하면서, 정확 구문 우선순위와 무관한
# 문서 배제를 되살린다.
#
# 2026-09-22(같은 리뷰 방식의 후속 감사) — 처음엔 상한을 0.6으로 뒀는데,
# `LARGE_SECTION_PENALTY`(0.5)보다 커서 이슈 #75가 막으려던 문제가 다단어 쿼리로
# 재발할 수 있었다: 거대 섹션(감점 대상)이 2단어 쿼리의 두 단어를 흩어진 채로만
# 갖고 있어도(연속 문구는 없음) 부분 보너스(0.6)가 감점(0.5)을 순이익 +0.1로
# 넘어서고, "같은 도메인이지만 무관"한 코사인 유사도(실측 ~0.27, 위 표 참고)만
# 더해져도 min_score(0.3)를 넘어 결과에 낀다(실측 재현: 0.27+0.1=0.37≥0.3). 이
# 프로젝트에서 `claim.keyword`는 5~15자짜리 짧은 구(`factcheck/claim.py`)라 2단어
# 쿼리가 예외가 아니라 주류이므로, 실사용에서 재발 가능성이 낮지 않다고 판단해
# 상한을 `LARGE_SECTION_PENALTY`보다 확실히 낮은 값(0.4)으로 내렸다.
#
# 2026-09-22 팀 리뷰(@9abin) 후속 지적·재수정 — 0.4도 여전히 `DEFAULT_MIN_SCORE`
# (0.3)보다 커서, 단어 겹침 판정 자체가 단어 경계 없는 substring 매칭이라는 점과
# 맞물려 새 구멍이 있었다: 검색어 "증자 발행"이 의미상 완전히 무관한 문서("무상증자"+
# "발행어음"처럼 단어 조각만 우연히 겹침)에서도 fraction=1.0(2/2)으로 잡혀, 코사인
# 유사도가 0이어도 보너스(0.4) 하나만으로 관련성 문턱(0.3)을 넘겼다(실측 재현).
# 한국어는 조사가 붙는 교착어라 진짜 단어 경계 검사로는 근본 해결이 안 되므로
# (같은 판단 근거: `dictionary/candidates.py::_is_accounting_note()` docstring),
# 대신 "부분 일치 보너스만으로는(코사인 기여가 전혀 없어도) 관련성 문턱을 절대
# 못 넘는다"는 조건을 새로 추가했다 — 상한을 `DEFAULT_MIN_SCORE`보다도 낮은 값으로
# 내려서, 우연한 단어 중첩이라도 최소한의 의미 유사도(코사인) 기여가 같이 있어야만
# 최종적으로 문턱을 넘을 수 있다. 이슈 #105가 고치려던 "흩어진 다단어도 반영" 자체는
# 유지된다(부분 보너스 자체는 여전히 순위를 끌어올림, 단 0부터 시작하는 관련성
# 판정을 단독으로 통과시키지는 못함).
DEFAULT_MIN_SCORE = 0.3
KEYWORD_MATCH_BONUS = 1.0
# 부분 일치의 상한 — 100% 겹쳐도 정확 구문(1.0)보다 낮고, LARGE_SECTION_PENALTY
# (0.5)·DEFAULT_MIN_SCORE(0.3)보다도 낮다(거대 섹션이 부분 일치만으로 감점을 못
# 넘게, 그리고 부분 일치만으로는 코사인 기여 없이 관련성 문턱도 못 넘게).
PARTIAL_KEYWORD_MATCH_CAP = 0.25
MIN_KEYWORD_OVERLAP_FRACTION = 0.5  # 이 비율 이하(과반 미달)로 겹치면 부분 점수 자체가 없다

# 이슈 #75 후속 — 실측(로컬 코퍼스 82,876개 섹션) 기준 섹션 길이 중앙값은 567자인데,
# 정기공시(사업보고서/분기보고서)의 "회사의 개요"·"사업의 내용"·"재무에 관한 사항",
# 증권발행_신고의 "모집 또는 매출에 관한 사항" 같은 섹션은 재무제표 표·약관 전문이
# 통째로 한 섹션이라 최대 150만자까지 나온다(20,000자 초과가 전체의 약 6%). B가 PR
# #77에서 verify_claim 프롬프트 렌더링 단계에 길이 상한(MAX_SECTION_CHARS)을 걸어
# TPM 초과 자체는 막았지만, 그건 이미 top_k에 뽑힌 뒤의 방어선이다 — 이 거대 섹션이
# 애매한 임베딩 유사도만으로 top_k 슬롯 하나를 차지하면, 더 짧고 실제로 루머와
# 관련 있는 서사형 섹션이 밀려날 수 있다(재무제표 숫자 표는 잘라봐야 팩트체크
# 근거로서의 가치가 원래 낮음). 그래서 여기서는 순위를 낮춰 "정확히 이 거대 섹션
# 안의 키워드를 찾는 게 아닌 이상" 우선순위에서 밀리게 한다 — 완전히 제외하지는
# 않는 이유는 KEYWORD_MATCH_BONUS로 만회 가능해야, 키워드가 진짜 그 섹션에만
# 있는 드문 경우까지 잃지 않기 때문이다(기존 "정확히 일치하면 반드시 반환" 원칙과
# 동일선상).
LARGE_SECTION_CHARS = 20_000
LARGE_SECTION_PENALTY = 0.5

logger = logging.getLogger(__name__)


def _validate_scoring_constants() -> None:
    """스코어링 상수 간 불변식을 모듈 로드 시 검증한다.

    두 불변식이 깨지면(부분 일치 상한이 거대 섹션 감점 이상이거나, 관련성 문턱
    이상이면) 위 2026-09-22 수정들이 조용히 무효화된다 — 다음에 이 상수들 중
    하나를 조정할 사람을 위한 가드.

    2026-09-22 팀 리뷰(@9abin) 지적 — 처음엔 이 가드를 모듈 최상단의 `assert`
    문으로 뒀는데, `python -O`/`PYTHONOPTIMIZE=1`로 실행하면 `assert` 문 자체가
    바이트코드에서 통째로 사라진다(Python 언어 고유의 동작, `__debug__`가
    `False`일 때). 이 가드가 이 코드베이스에서 프로덕션 불변식을 assert 하나로만
    지키는 유일한 사례였는데, 누군가 나중에 이 상수들을 조정하면서 `-O` 환경에서
    가드가 무력화된다는 걸 모르면 테스트는 그대로 통과해도 운영에서는 조용히
    재발할 수 있다. `assert` 대신 항상 실행되는 일반 함수 호출로 바꿔 `-O`와
    무관하게 항상 검증되게 한다.
    """
    if not PARTIAL_KEYWORD_MATCH_CAP < LARGE_SECTION_PENALTY:
        raise AssertionError(
            "부분 일치 상한이 거대 섹션 감점 이상이면 거대 섹션이 부분 일치만으로"
            " 감점을 넘을 수 있다"
        )
    if not PARTIAL_KEYWORD_MATCH_CAP < DEFAULT_MIN_SCORE:
        raise AssertionError(
            "부분 일치 상한이 관련성 문턱 이상이면 코사인 유사도 기여가 전혀 없어도"
            " 단어가 우연히 겹치기만 하면 관련성 문턱을 넘을 수 있다"
        )


_validate_scoring_constants()


def _keyword_bonus_for(section: DisclosureSection, needle: str, needle_words: list[str]) -> float:
    """`needle`(소문자화된 검색어) 전체가 섹션 제목·본문에 그대로 있으면 만점을 준다
    (기존 "정확히 일치하면 반드시 반환" 보장, 단어 하나짜리 검색어는 `needle_words
    == [needle]`이라 사실상 이 분기만 탄다).

    그렇지 않으면 공백 기준 단어 겹침 비율(이슈 #105)로 부분 점수를 주되, R106-1/
    R106-2 수정으로 두 조건을 더 건다: 겹치는 비율이 과반(`MIN_KEYWORD_OVERLAP_
    FRACTION`)을 넘어야 하고(핵심어 없이 흔한 단어 하나만 겹쳐 관련성 문턱을 넘는
    경로 차단), 상한을 정확 구문보다 낮게(`PARTIAL_KEYWORD_MATCH_CAP`) 캡한다(흩어진
    단어가 100% 겹쳐도 정확 구문과 동점이 되지 않게 해 우선순위를 보장).

    알려진 한계(2026-09-22 감사·팀 리뷰(@9abin)에서 확인, 수정 보류) — 단어 겹침
    판정이 단어 경계를 안 보는 부분 문자열 검사라, 드물게 무관한 복합어 안에
    우연히 포함돼도 "매치"로 잡힐 수 있다("증자 발행" 검색이 "무상증자"+"발행어음"
    에 단어 조각만 겹쳐 잡히는 사례). 한글은 조사가 공백 없이 붙는 교착어라
    정규식 단어 경계(`\\b`)로는 못 고친다(형태소 분석기가 있어야 근본 해결 — 이
    모듈이 이미 정규식 휴리스틱의 한계로 인정하고 있는 것과 같은 범주,
    `dictionary/candidates.py::_is_accounting_note()` docstring 참고). 대신
    "부분 일치 보너스만으로는(코사인 기여 없이) 관련성 문턱을 못 넘는다"는 조건을
    추가해(`PARTIAL_KEYWORD_MATCH_CAP < DEFAULT_MIN_SCORE`) 완전히 무관한 문서가
    새는 것 자체는 막는다 — 우연한 단어 중첩을 원천 차단하지는 못해도, 그것만으로
    관련 문서로 취급되지는 않는다.

    `needle_words`에 같은 단어가 중복되면(예: 같은 단어를 두 번 포함한 검색어)
    겹침 비율 계산이 왜곡될 수 있어 `set()`으로 미리 중복을 제거한다 — 실사용
    경로(`claim.keyword`, 5~15자 짧은 구)에서는 거의 안 생기는 입력이지만, 비용이
    거의 없어 방어적으로 남겨둔다.
    """
    title = section.section_title.lower()
    if needle in title:
        # 제목만으로 이미 정확히 일치하면, 최대 150만자(전체 섹션의 약 6%)까지
        # 나오는 본문을 굳이 소문자 변환·결합할 필요가 없다 — 거대 섹션에서
        # 흔한 win(이슈 #75 맥락과 동일선상의 성능 고려).
        return KEYWORD_MATCH_BONUS
    haystack = f"{title}\n{section.text.lower()}"
    if needle in haystack:
        return KEYWORD_MATCH_BONUS
    if not needle_words:
        return 0.0
    unique_words = set(needle_words)
    fraction = sum(1 for word in unique_words if word in haystack) / len(unique_words)
    if fraction <= MIN_KEYWORD_OVERLAP_FRACTION:
        return 0.0
    return PARTIAL_KEYWORD_MATCH_CAP * fraction


class CompanyDisclosureIndex:
    """corp_code 기준 하이브리드(키워드+임베딩) 검색 인덱스.

    임베딩 백엔드 확정(PR #27, 2026-09-11: local + dragonkue/BGE-m3-ko + 1024차원)에
    따라 키워드 부분 문자열 매칭 + 코사인 유사도를 결합한다.

    스코어 = cosine_similarity(query, section) + (KEYWORD_MATCH_BONUS if 키워드가
    section_title/text 에 부분 문자열로 포함되면) - (LARGE_SECTION_PENALTY if 섹션
    텍스트가 LARGE_SECTION_CHARS 자를 넘으면). `min_score` 미만은 결과에서 제외한다
    — 임베딩만으로 항상 top_k 를 채우면(진짜 무관한 것까지 순위만 매겨 반환) 검색
    품질이 오히려 나빠지기 때문. 키워드 그대로 포함된 섹션은 보너스 덕에 항상
    문턱을 넘어 반환된다(기존 순수 키워드 검색 동작과 하위 호환). 재무제표류 거대
    섹션은 감점을 얹어 순위를 낮추되(이슈 #75), 키워드가 정확히 그 안에만 있으면
    보너스로 감점을 상쇄하고 여전히 반환될 수 있다.

    점수가 동률이면(실무에서는 드묾) `rcept_dt`(Issue #32/DEC-005) 최신순으로
    tie-break한다 — 관련도가 랭킹의 주 기준이고 날짜는 보조 기준이다(PR #34를 #35
    위로 리베이스하며 합의된 방식).

    `search()`는 원래 C(우지훈) 담당인 "조회 연동"의 일부지만, 인덱스가 실제로 동작하는지
    검증하려면 최소한의 검색이 필요해서 A(이승노)가 PR #35에서 하이브리드 랭킹까지
    구현해뒀다 — C가 조회 연동 작업 때 스코어링 공식/문턱값을 다듬거나 대체해도 된다.
    """

    def __init__(
        self,
        *,
        lazy_loader: Callable[[CompanyDisclosureIndex, str], None] | None = None,
    ) -> None:
        """`lazy_loader`(2026-09-18, 콜드스타트 지연 로딩): 부팅 시 전체 코퍼스를
        미리 임베딩하는 대신, `search()`가 아직 안 본 `corp_code`를 요청받았을 때만
        그 자리에서 채워 넣게 하는 훅이다 — 실측(demo/app.py) 기준 100개사 전체를
        미리 임베딩하면 40.6분이 걸리는데, 실제 사용자는 한 번에 회사 하나만
        묻는다. `lazy_loader(index, corp_code)`는 이 인덱스 인스턴스를 직접
        `index()`/`index_with_vectors()`로 채우는 부작용 함수다(반환값 없음) —
        디스크 캐시 적중 시 재임베딩을 피하려면 어느 쪽을 부를지 호출부가 판단해야
        해서, "섹션 리스트를 리턴"하는 것보다 "인덱스를 직접 채우는" 형태가 맞다.
        `None`이면(기본값) 예전과 동일하게 미리 채워진 상태에서만 동작한다 —
        하위 호환.
        """
        self._by_corp: dict[str, list[DisclosureSection]] = defaultdict(list)
        self._faiss_by_corp: dict[str, faiss.IndexFlatIP] = {}
        self._indexed_keys: dict[str, set[tuple[str, str]]] = defaultdict(set)
        self._lazy_loader = lazy_loader
        # lazy_loader를 이미 시도해본 corp_code 집합 — 코퍼스에 실제로 없는 회사(로더가
        # 아무것도 못 채움)를 매 검색마다 다시 시도하지 않기 위함(_by_corp가 비어있는
        # "안 불러옴"과 "불러왔지만 데이터가 없음"을 구분).
        self._lazy_loaded: set[str] = set()
        # 2026-09-22(상세 감사) — corp_code별 락. 원래 `search()`의 지연 로딩
        # 체크-후-실행(261행 부근)과 `index()`/`index_with_vectors()`의 체크-후-추가가
        # 전부 락 없이 실행돼, 두 세션이 거의 동시에 같은(한 번도 안 불러온) 종목을
        # 조회하면 같은 섹션이 중복 인덱싱될 수 있었다(스레드 시뮬레이션으로 재현:
        # 5/5 트라이얼에서 섹션이 2배로 중복 저장, 그 상태가 디스크 캐시에도 영구
        # 기록됨). `RLock`인 이유: `search()`가 락을 잡은 채로 `_lazy_loader`를
        # 호출하는데, 그 로더가 내부적으로 다시 `index()`(같은 corp_code, 같은
        # 스레드)를 호출해 같은 락을 재획득한다 — 일반 `Lock`이면 여기서 자기 자신과
        # 데드락한다. corp_code 단위로 나눠서 서로 다른 회사 조회끼리는 여전히
        # 병렬로 동작한다(전역 락 하나로 다 묶지 않음).
        self._corp_locks: dict[str, threading.RLock] = {}
        self._corp_locks_guard = threading.Lock()

    def _lock_for(self, corp_code: str) -> threading.RLock:
        """`corp_code` 전용 락을 돌려준다(없으면 만든다) — 락 딕셔너리 자체에 대한
        동시 접근은 별도의 전역 가드(`_corp_locks_guard`)로 짧게만 보호한다."""
        with self._corp_locks_guard:
            lock = self._corp_locks.get(corp_code)
            if lock is None:
                lock = threading.RLock()
                self._corp_locks[corp_code] = lock
            return lock

    def index(self, sections: list[DisclosureSection]) -> None:
        """섹션을 corp_code 별로 적재하고 임베딩을 계산해 쌓는다. 누적 가능.

        같은 `(rcept_no, section_title)` 조합이 이미 적재돼 있으면 건너뛴다(Issue #80)
        — 재인덱싱·증분 업데이트 중 실수로 같은 섹션이 두 번 들어와도 검색 결과가
        중복되거나 코사인 랭킹이 왜곡되지 않도록 막는다. 한 `rcept_no`가 여러 섹션을
        낼 수 있어 `section_title`까지 묶어야 서로 다른 섹션을 오탐하지 않는다.

        `faiss.IndexFlatIP.add()`가 원래 numpy `vstack` 누적 로직을 그대로 대체한다 —
        기존/신규 구분 없이 그냥 더하면 되므로 오히려 더 단순해졌다.
        """
        by_corp: dict[str, list[DisclosureSection]] = defaultdict(list)
        for section in sections:
            by_corp[section.corp_code].append(section)

        for corp_code, corp_sections in by_corp.items():
            with self._lock_for(corp_code):
                existing_keys = self._indexed_keys[corp_code]
                new_sections = []
                for section in corp_sections:
                    key = (section.rcept_no, section.section_title)
                    if key in existing_keys:
                        logger.warning(
                            "CompanyDisclosureIndex 중복 섹션 무시 (corp_code=%s, "
                            "rcept_no=%s, section_title=%s)",
                            corp_code,
                            section.rcept_no,
                            section.section_title,
                        )
                        continue
                    existing_keys.add(key)
                    new_sections.append(section)

                if not new_sections:
                    continue

                texts = [f"{s.section_title}\n{s.text}" for s in new_sections]
                new_vectors = np.ascontiguousarray(embed_texts(texts), dtype=np.float32)

                self._by_corp[corp_code].extend(new_sections)
                faiss_index = self._faiss_by_corp.get(corp_code)
                if faiss_index is None:
                    faiss_index = faiss.IndexFlatIP(new_vectors.shape[1])
                    self._faiss_by_corp[corp_code] = faiss_index
                faiss_index.add(new_vectors)

    def index_with_vectors(self, sections: list[DisclosureSection], vectors: np.ndarray) -> None:
        """이미 계산된 벡터로 섹션을 적재한다(임베딩 재계산 없음).

        `index()`와 같은 누적·중복 스킵 규칙을 따르지만 `embed_texts()`를 부르지
        않는다 — 회사별 지연 로딩 디스크 캐시(`cache_store.py`)에서 이미 임베딩된
        (섹션, 벡터)를 불러올 때 쓴다. `vectors`는 `sections`와 같은 순서로
        1:1 대응해야 한다.
        """
        by_corp: dict[str, list[DisclosureSection]] = defaultdict(list)
        vector_rows_by_corp: dict[str, list[int]] = defaultdict(list)
        for i, section in enumerate(sections):
            by_corp[section.corp_code].append(section)
            vector_rows_by_corp[section.corp_code].append(i)

        for corp_code, corp_sections in by_corp.items():
            with self._lock_for(corp_code):
                existing_keys = self._indexed_keys[corp_code]
                new_sections = []
                new_row_indices = []
                for section, row in zip(corp_sections, vector_rows_by_corp[corp_code], strict=True):
                    key = (section.rcept_no, section.section_title)
                    if key in existing_keys:
                        continue
                    existing_keys.add(key)
                    new_sections.append(section)
                    new_row_indices.append(row)

                if not new_sections:
                    continue

                new_vectors = np.ascontiguousarray(vectors[new_row_indices], dtype=np.float32)
                self._by_corp[corp_code].extend(new_sections)
                faiss_index = self._faiss_by_corp.get(corp_code)
                if faiss_index is None:
                    faiss_index = faiss.IndexFlatIP(new_vectors.shape[1])
                    self._faiss_by_corp[corp_code] = faiss_index
                faiss_index.add(new_vectors)

    def dump_rows_for(self, corp_code: str) -> tuple[list[DisclosureSection], np.ndarray]:
        """`dump_rows()`와 같지만 한 회사분만 뽑는다 — 회사별 지연 로딩 캐시
        저장용(전체를 매번 다시 직렬화할 필요 없이, 방금 새로 채운 회사 하나만
        디스크에 남긴다). `dump_rows()`와 동일하게 `reconstruct_n()`으로 FAISS가
        들고 있는 원본 벡터를 그대로 꺼낸다."""
        sections = list(self._by_corp.get(corp_code, []))
        faiss_index = self._faiss_by_corp.get(corp_code)
        if faiss_index is None:
            vectors = np.zeros((0, 0), dtype=np.float32)
        else:
            vectors = faiss_index.reconstruct_n(0, faiss_index.ntotal)
        return sections, vectors

    def dump_rows(self) -> tuple[list[DisclosureSection], np.ndarray]:
        """직렬화용(디스크 캐싱, `cache_store.py`) — corp_code 그룹 순서대로 펼친 섹션과
        그와 1:1 대응하는 벡터. `load_rows()`가 이 순서 그대로 다시 넣으면 `index()`로
        채운 것과 동일한 상태가 된다.

        `faiss.IndexFlatIP`는 원본 벡터를 그대로 들고 있어(양자화 없음) `reconstruct_n()`
        으로 다시 꺼낼 수 있다 — 벡터를 FAISS와 별도로 또 저장해둘 필요가 없다."""
        sections: list[DisclosureSection] = []
        vector_chunks: list[np.ndarray] = []
        for corp_code, corp_sections in self._by_corp.items():
            sections.extend(corp_sections)
            faiss_index = self._faiss_by_corp[corp_code]
            vector_chunks.append(faiss_index.reconstruct_n(0, faiss_index.ntotal))
        vectors = np.vstack(vector_chunks) if vector_chunks else np.zeros((0, 0), dtype=np.float32)
        return sections, vectors

    def load_rows(self, sections: list[DisclosureSection], vectors: np.ndarray) -> None:
        """`dump_rows()`가 만든 (섹션, 벡터)로 인덱스를 재구성한다(임베딩 재계산 없음).

        디스크 캐시 로드 전용 — `sections`/`vectors`는 반드시 `dump_rows()`가 만든 순서
        그대로여야 corp_code별 벡터 슬라이스가 올바르게 맞는다. 기존 상태는 통째로
        교체한다(누적 아님) — `_indexed_keys`도 로드된 섹션 기준으로 다시 채워서,
        로드 후 `index()`를 추가로 호출해도 Issue #80 중복 스킵이 정상 동작한다."""
        self._by_corp = defaultdict(list)
        self._faiss_by_corp = {}
        self._indexed_keys = defaultdict(set)
        by_corp: dict[str, list[DisclosureSection]] = defaultdict(list)
        for section in sections:
            by_corp[section.corp_code].append(section)

        offset = 0
        for corp_code, corp_sections in by_corp.items():
            n = len(corp_sections)
            self._by_corp[corp_code] = corp_sections
            corp_vectors = np.ascontiguousarray(vectors[offset : offset + n], dtype=np.float32)
            faiss_index = faiss.IndexFlatIP(corp_vectors.shape[1])
            faiss_index.add(corp_vectors)
            self._faiss_by_corp[corp_code] = faiss_index
            for section in corp_sections:
                self._indexed_keys[corp_code].add((section.rcept_no, section.section_title))
            offset += n

    def search(
        self,
        corp_code: str,
        keyword: str,
        *,
        top_k: int = 10,
        min_score: float = DEFAULT_MIN_SCORE,
    ) -> list[DisclosureSection]:
        """점수 없이 섹션만 반환하는 기존 검색 인터페이스."""
        return [
            hit.section
            for hit in self.search_with_scores(corp_code, keyword, top_k=top_k, min_score=min_score)
        ]

    def search_with_scores(
        self,
        corp_code: str,
        keyword: str,
        *,
        top_k: int = 10,
        min_score: float = DEFAULT_MIN_SCORE,
    ) -> list[SearchHit]:
        """corp_code 로 좁힌 뒤 keyword 기반 하이브리드 점수로 정렬해 top_k 개 반환한다.

        `keyword`가 빈 문자열이면 임베딩 계산 없이 `rcept_dt` 최신순으로 top_k 개
        반환한다(검색어가 없을 때 "전체 최근 섹션 보기" 용도, Issue #32/DEC-005).

        `corp_code`를 아직 안 봤고 `lazy_loader`가 설정돼 있으면, 검색 전에 그
        회사만 채워 넣는다(2026-09-18 지연 로딩) — 회사가 실제로 이미 비어있는
        것(코퍼스에 없는 회사)과 "아직 안 불러온 것"을 구분해야 하므로, 이미 시도한
        적 있는 회사는 다시 부르지 않는다(`_lazy_loaded` 참고).

        2026-09-22(상세 감사) — 이 체크-후-실행이 락 없이 실행되면, 같은 종목을
        거의 동시에 조회하는 두 요청이 둘 다 통과해 `_lazy_loader`를 중복 호출할
        수 있다(재현: 스레드 시뮬레이션 5/5). `corp_code` 전용 락으로 감싸고,
        락을 기다리는 동안 다른 스레드가 이미 로딩을 끝냈을 수 있으므로 락을 잡은
        뒤 한 번 더 확인한다(double-checked locking).
        """
        if (
            corp_code not in self._by_corp
            and corp_code not in self._lazy_loaded
            and self._lazy_loader is not None
        ):
            with self._lock_for(corp_code):
                if corp_code not in self._lazy_loaded:
                    self._lazy_loaded.add(corp_code)
                    self._lazy_loader(self, corp_code)

        candidates = self._by_corp.get(corp_code, [])
        if not keyword:
            ranked_by_date = sorted(candidates, key=lambda s: s.rcept_dt, reverse=True)
            return [SearchHit(section, None) for section in ranked_by_date[:top_k]]
        if not candidates:
            return []

        needle = keyword.lower()
        needle_words = needle.split()
        keyword_bonus = np.array(
            [_keyword_bonus_for(section, needle, needle_words) for section in candidates]
        )

        faiss_index = self._faiss_by_corp.get(corp_code)
        if faiss_index is not None and faiss_index.ntotal == len(candidates):
            query = np.ascontiguousarray(embed_query(keyword), dtype=np.float32).reshape(1, -1)
            scores, ids = faiss_index.search(query, faiss_index.ntotal)
            cosine_sims = np.zeros(len(candidates), dtype=np.float32)
            cosine_sims[ids[0]] = scores[0]  # FAISS는 점수순 반환 — 원래 후보 순서로 재배열
        else:
            # 정상 흐름에서는 index()가 섹션 리스트와 벡터를 항상 같은 길이로 쌓으므로
            # 여기 들어오면 내부 상태가 깨졌다는 뜻(Issue #80) — 의미 검색이 조용히
            # 무력화되고 키워드 매칭만 남으므로, 원인 추적이 가능하도록 경고를 남긴다.
            vector_count = 0 if faiss_index is None else faiss_index.ntotal
            logger.warning(
                "CompanyDisclosureIndex 내부 상태 불일치 (corp_code=%s): "
                "벡터 %d개 vs 섹션 %d개 — 의미 검색을 건너뛰고 키워드 매칭만 사용합니다.",
                corp_code,
                vector_count,
                len(candidates),
            )
            cosine_sims = np.zeros(len(candidates))

        large_section_penalty = np.array(
            [
                LARGE_SECTION_PENALTY if len(section.text) > LARGE_SECTION_CHARS else 0.0
                for section in candidates
            ]
        )

        scores = cosine_sims + keyword_bonus - large_section_penalty
        eligible = [i for i in range(len(candidates)) if scores[i] >= min_score]
        ranked = sorted(
            eligible,
            key=lambda i: (scores[i], candidates[i].rcept_dt),
            reverse=True,
        )
        return [SearchHit(candidates[i], float(scores[i])) for i in ranked[:top_k]]
