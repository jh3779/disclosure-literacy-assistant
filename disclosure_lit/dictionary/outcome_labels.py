"""사후 이벤트(A12) 라벨 만들기 — 섹션·이벤트·클러스터 인덱스를 `OutcomeLabel`로.

`dictionary/stats.py`의 집계가 먹는 입력을 만드는 단계다. Streamlit이나 평가 CLI 같은
실행 환경을 모르는 순수 함수라, 화면(L4)과 평가(L5)가 같은 코드를 공유한다.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from disclosure_lit.dart.ingest import DisclosureSection
from disclosure_lit.dictionary.candidates import extract_phrase_candidates
from disclosure_lit.dictionary.stats import OutcomeLabel

if TYPE_CHECKING:  # 런타임 import를 피한다 — retrieval이 dictionary.cluster를 쓰기 때문.
    from disclosure_lit.retrieval.phrase_index import PhraseClusterIndex


def build_outcome_labels(
    sections: list[DisclosureSection],
    post_events: dict[str, list[str]],
    index: PhraseClusterIndex,
) -> list[OutcomeLabel]:
    """섹션·사후 이벤트(A12)·이미 빌드된 클러스터 인덱스로 OutcomeLabel 목록을 만든다.

    2026-09-24 이사: 원래 `demo/app.py::_build_outcome_labels()`였는데, 평가(L5,
    `eval/l2b_holdout.py`)가 같은 계산을 쓰려고 화면 모듈의 비공개 함수를 import하고
    있었다(L5 -> L4 역방향). 계산 자체는 L2b 도메인 로직이라 이쪽으로 옮기고, 화면과
    평가가 나란히 이 함수를 쓰게 했다 — `tests/test_layer_boundaries.py` 참고.

    순수 함수로 뺀 이유: `_outcome_labels()`(아래)는 `@st.cache_resource`가 붙어 있어
    Streamlit 스크립트 실행 컨텍스트 밖(단위 테스트)에서 캐시 동작이 불안정하고,
    `_phrase_index()`를 실제로 호출하면 무거운 로컬 임베딩 모델이 필요하다 — 매칭 로직
    자체는 이 함수로 분리해 가짜 index(테스트 더블)로 빠르게 단위 테스트한다.

    성능: `PhraseClusterIndex.match()`를 문구 하나하나(공시 수만 건 x 문구 여러 개)에
    그대로 반복 호출하면 실전에서 못 쓸 정도로 느리다 — `embed_query()`가 호출마다
    단건 배치로 모델을 부르는데, 이 디스패치 오버헤드가 실제 연산량을 압도한다(PR #57
    실측, A: 실코퍼스에서 5분 동안 14초치 연산만 진행돼 완주까지 몇 시간이 걸릴 걸로
    추정됨). 그래서 먼저 이 섹션들에 등장하는 고유 문구를 전부 모아 `match_many()`로
    **한 번만** 배치 매칭하고, 그 결과(문구 -> cluster_id)를 각 섹션에 다시 배분한다.

    중복 제거: 한 공시(섹션) 안에 의미상 비슷한 문장이 여러 개 있으면(예: "선제적
    재무구조 개선을 위해..."와 "지속적으로 재무구조 개선을...") 서로 다른 문구가 같은
    클러스터로 매칭될 수 있고, 더 나아가 **같은 회사가 여러 공시에서 같은 상투구를
    반복**하는 경우도 실측으로 확인됐다(예: "OO을 참조하시기 바랍니다." 류 정형 문구
    하나를 한 회사가 21번 반복 제출 — PR #62 후속 발견). 두 경우 다 그대로 라벨을
    만들면 `compute_stats()`가 독립적인 증거 여러 건으로 잘못 세게 된다(첫 번째는
    `sample_count`가 실제 공시 건수보다, 두 번째는 실제 독립 회사 수보다 부풀려짐 —
    pseudoreplication). `(corp_code, cluster_id, outcome_label)` 단위로 dedup해서 한
    회사는 한 클러스터·한 outcome에 최대 1건만 기여하도록 한다 — 같은 회사가 서로
    다른 outcome을 여러 개 보였다면(예: 영업정지도, 제재_벌금도 있었다면) 그건 각각
    남긴다(`compute_stats()`가 회사 단위로 다시 집계하므로 여기서 완전히 압축하지
    않아도 되지만, `_labels` 크기를 줄여두면 이후 집계가 가벼워진다).
    """
    relevant: list[tuple[DisclosureSection, list[str], list[str]]] = []
    unique_phrases: dict[str, None] = {}  # 등장 순서를 보존하는 유사-집합
    for section in sections:
        events = post_events.get(section.rcept_no)
        if not events:
            continue
        phrases = extract_phrase_candidates([section])
        relevant.append((section, events, phrases))
        for phrase in phrases:
            unique_phrases.setdefault(phrase, None)

    phrase_list = list(unique_phrases)
    matched_clusters = index.match_many(phrase_list)
    phrase_to_cluster: dict[str, str | None] = {
        phrase: (cluster.cluster_id if cluster is not None else None)
        for phrase, cluster in zip(phrase_list, matched_clusters, strict=True)
    }

    seen: set[tuple[str, str, str]] = set()  # (corp_code, cluster_id, outcome_label)
    labels: list[OutcomeLabel] = []
    for section, events, phrases in relevant:
        for phrase in phrases:
            cluster_id = phrase_to_cluster[phrase]
            if cluster_id is None:
                continue
            for event in events:
                dedup_key = (section.corp_code, cluster_id, event)
                if dedup_key in seen:
                    continue
                seen.add(dedup_key)
                labels.append(
                    OutcomeLabel(
                        cluster_id=cluster_id, corp_code=section.corp_code, outcome_label=event
                    )
                )
    return labels
