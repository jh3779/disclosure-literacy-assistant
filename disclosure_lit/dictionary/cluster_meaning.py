"""클러스터의 "의미" — 대표 문구가 어떤 form_type(공시 서식 유형)에서 주로 나타나는가.

2026-09-17: 상투구 클러스터가 "미래 사후 이벤트"를 예측한다는 원래 가설은 held-out
검증으로 반증됐다(재현율 0%, 임계값을 조정해도 마찬가지 — 대화 기록 참고). 반면
"이 클러스터가 어떤 form_type에서 주로 나타나는가"는 예측이 아니라 분류에 가까운
질문이라 훨씬 안정적으로 성립한다. 이 모듈은 그 분류만 다룬다 — "이 표현을 쓰면 X가
일어난다"가 아니라 "이 표현은 X 유형 공시에서 흔히 나타난다"는 서술적 사실만 반환한다.

**순도 수치 해석 주의(2026-09-21 재측정, 회사 수 필터가 있던 시절)**: 순도>=0.5인
클러스터 97.8%, >=0.8은 45.1%(114개사, 후보 1,875개, 당시 `min_companies=5`로
걸러진 1,451개 클러스터 기준). 그런데 같은 후보군을 클러스터링 없이 문자열 그대로
묶은 기준선도 96.7%/46.2%였다 — 즉 이 순도는 클러스터링이 잘 됐다는 증거라기보다
"반복되는 상투구는 원래 특정 서식에 고정돼 있다"는 데이터 성질을 재는 지표다. 순도가
낮은 클러스터는 감사위원회 조항처럼 여러 서식(주주총회_소집·의결권대리행사권유·
증권발행_신고·정기공시)에 법정으로 공통인 문구가 대부분이었다. 예전 docstring의
"98%/48%"는 기준선 없이 절대값만 본 것이라 근거로 쓰지 않는다.

**2026-09-21 추가 변경**: 회사 수 필터를 클러스터링에서 빼고(아래 `MIN_COMPANIES_
FOR_MEANING`) 신뢰도 판정으로 옮기면서, 모든 클러스터(4,352개, `MIN_OCCURRENCES_
FOR_MEANING` 이상만 추리면 4,064개)를 대상으로 순도를 계산하게 됐다 — 위 97.8%/
45.1%는 그 전(회사 수로 이미 한 번 걸러진 1,451개) 기준이라 모집단이 다르다.
효과는 골드셋으로 직접 확인했다 — 근거 공시 섹션 매칭이 26/33(케이스 19/25)에서
**33/33(케이스 25/25)**로 올라갔다. 순도 수치 자체를 새 모집단으로 다시 재는 건
후속 작업(수치가 바뀌어도 이 문단의 핵심 결론 — "순도는 데이터 성질을 잰다" —
은 안 바뀔 걸로 예상하지만, 실측 없이 단정하지 않는다).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass

from disclosure_lit.dart.ingest import DisclosureSection
from disclosure_lit.dictionary.candidates import MIN_COMPANIES_DEFAULT, extract_phrase_candidates
from disclosure_lit.retrieval.phrase_index import PhraseClusterIndex

# 순도가 이 미만이면 "이 클러스터의 의미"를 사용자에게 보여주지 않는다 — 여러
# form_type에 흩어진 클러스터(실측상 여러 서식에 법정으로 공통인 조항이 대부분)를
# 특정 유형으로 잘못 단정하지 않기 위함(실측치는 모듈 docstring 참고 — 최신 모집단
# 기준 재측정은 후속 작업).
MIN_PURITY_TO_DISPLAY = 0.5

# 발생 횟수가 이 미만이면 순도 계산 자체가 통계적으로 무의미하다(1~2번 등장한
# 클러스터는 우연히 100% 순도가 나올 수 있음).
MIN_OCCURRENCES_FOR_MEANING = 3

# 2026-09-21(팀 리뷰 지적) — 예전엔 `PhraseClusterIndex.build(min_companies=5)`가
# 표본 적은 클러스터를 매칭 대상에서 통째로 뺐다. "이 표현이 뭔지 찾는 것"과 "여러
# 회사 통계로 보여줄 자격이 있는지"는 다른 질문이라, 이제 매칭은 항상 성공하고(클러스터
# 자체는 안 버림) 신뢰도만 여기서 회사 수로 따로 매긴다 — `MIN_COMPANIES_DEFAULT`
# (candidates.py, "여러 무관한 회사가 같은 말을 쓰면 업계 표준 화법일 가능성이 높다")
# 를 그대로 재사용한다. 희귀 문구(회사 1~2곳)도 클러스터는 존재하고 대표 문구·분류는
# 나오지만, `is_reliable=False`라 "N개 회사가 흔히 쓰는 표현"이라는 신뢰도 높은
# 주장은 안 하는 쪽으로 화면에서 걸러진다(호출부 책임 — 이 모듈은 신호만 준다).
MIN_COMPANIES_FOR_MEANING = MIN_COMPANIES_DEFAULT


@dataclass(frozen=True)
class ClusterMeaning:
    cluster_id: str
    representative_phrase: str
    dominant_form_type: str
    purity: float  # 발생 중 dominant_form_type이 차지하는 비율
    occurrence_count: int  # 순도 계산에 쓰인 총 발생 횟수
    company_count: int  # 이 클러스터의 멤버 문구를 실제로 쓴 서로 다른 회사 수
    is_reliable: bool  # purity/occurrence_count/company_count 세 조건을 모두 만족


def compute_cluster_meanings(
    index: PhraseClusterIndex,
    candidate_phrases: list[str],
    sections: list[DisclosureSection],
) -> dict[str, ClusterMeaning]:
    """클러스터별로 "어떤 form_type에서 주로 나타나는가"를 계산한다.

    문구->클러스터 매핑은 `index.member_cluster_ids()`(build()가 남긴 정확한 소속,
    2026-09-21)를 우선 쓴다. 디스크 캐시에서 멤버십 없이 로드된 인덱스면 예전처럼
    후보 문구 전체를 `match_many()`로 centroid에 재매칭해 근사 복원한다(후보 전체를
    다시 임베딩하므로 느리고, 대표 문구 근사 centroid 시절엔 4.5%가 틀렸다 — 지금은
    진짜 centroid라 실측 불일치 0). 그 다음 전체 섹션을 훑어(순수 문자열 매칭, 임베딩
    불필요) 각 클러스터 멤버 문구가 어느 form_type의 섹션에서, 몇 개 회사에서
    나오는지 센다(회사 수는 `MIN_COMPANIES_FOR_MEANING` 신뢰도 판정에 쓴다).
    """
    clusters, _ = index.dump()
    if not clusters or not candidate_phrases:
        return {}
    cluster_by_id = {c.cluster_id: c for c in clusters}

    member_ids = index.member_cluster_ids()
    if member_ids is not None:
        phrase_to_cluster_id = {
            phrase: member_ids[phrase] for phrase in candidate_phrases if phrase in member_ids
        }
    else:
        matched = index.match_many(candidate_phrases)
        phrase_to_cluster_id = {
            phrase: cluster.cluster_id
            for phrase, cluster in zip(candidate_phrases, matched, strict=True)
            if cluster is not None
        }
    if not phrase_to_cluster_id:
        return {}

    counts: dict[str, Counter] = defaultdict(Counter)
    companies: dict[str, set[str]] = defaultdict(set)
    for section in sections:
        for phrase in extract_phrase_candidates([section]):
            cluster_id = phrase_to_cluster_id.get(phrase)
            if cluster_id is None:
                continue
            counts[cluster_id][section.form_type] += 1
            companies[cluster_id].add(section.corp_code)

    meanings: dict[str, ClusterMeaning] = {}
    for cluster_id, form_type_counts in counts.items():
        total = sum(form_type_counts.values())
        if total < MIN_OCCURRENCES_FOR_MEANING:
            continue
        dominant_form_type, dominant_count = form_type_counts.most_common(1)[0]
        purity = dominant_count / total
        company_count = len(companies[cluster_id])
        cluster = cluster_by_id[cluster_id]
        meanings[cluster_id] = ClusterMeaning(
            cluster_id=cluster_id,
            representative_phrase=cluster.representative_phrase,
            dominant_form_type=dominant_form_type,
            purity=purity,
            occurrence_count=total,
            company_count=company_count,
            is_reliable=(
                purity >= MIN_PURITY_TO_DISPLAY and company_count >= MIN_COMPANIES_FOR_MEANING
            ),
        )
    return meanings
