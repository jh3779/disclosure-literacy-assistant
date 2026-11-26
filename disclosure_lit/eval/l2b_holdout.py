"""L2b(화법통계) held-out 검증 — Issue #66. 담당: A.

**왜 필요한가**: `compute_all_stats()`(FDR 보정)를 붙여도, 그건 "지금 이 코퍼스
안에서 다중비교를 통제했다"는 뜻일 뿐 "다른 회사에도 재현되는 진짜 신호"라는 증거는
아니다 — 같은 데이터로 가설을 찾고 같은 데이터로 검증하면 순환논증이다(이전에
factcheck gold set에서 지적된 것과 같은 문제, docs 대화 참고). L2b도 똑같은 함정이
있다: 지금 "유의미"하다고 나온 클러스터가 우연인지 진짜인지는, 그 클러스터를 찾을 때
안 쓴 회사들로 다시 검정해봐야만 알 수 있다.

**방법 — split-half(재클러스터링 없음) + 상위 클러스터만 집중 검증**:
회사(corp_code) 단위로 코퍼스를 절반씩 나눈다(같은 회사의 라벨이 train/holdout에
걸쳐 쪼개지면 안 된다 — `dictionary/stats.py`의 회사 단위 중복 제거 원칙과 동일).
학습 절반에서 `compute_all_stats()`로 유의미하다고 나온 (cluster_id, outcome_label)
조합만, 검증 절반에서 같은 조합이 기저율보다 유의하게 다시 나오는지 재검정한다.

**재클러스터링을 안 하는 이유**: `PhraseClusterIndex.build()`는 2026-09-21부터 입력
순서와 무관한 결정적 알고리즘(응집형 클러스터링, `dictionary/cluster.py` 모듈
docstring)이지만, 그건 "같은 문구 집합이면 항상 같은 클러스터가 나온다"는 보장일
뿐이다 — 코퍼스를 절반씩 나누면 각 절반에 들어오는 문구 집합 자체가 달라지므로,
절반씩 따로 다시 클러스터링하면 알고리즘이 결정적이어도 cluster_id/클러스터 구성이
갈려 "같은 클러스터"를 비교한다고 보장할 수 없다. 그래서 클러스터는 전체 코퍼스로
한 번만 빌드해 고정하고(호출부가 이미 만들어둔 `PhraseClusterIndex`를 그대로 재사용),
`OutcomeLabel`(회사 단위)만 train/holdout으로 나눈다 — 표본 부족 문제와 "절반만 다시
클러스터링하면 기준 자체가 흔들리는" 문제를 동시에 다루려 하지 않고, held-out 검증은
후자를 우회하는 쪽으로 범위를 좁혔다.

**상위 클러스터만 보는 이유**: 클러스터 전체(수백 개)를 다시 held-out 검정하면
그 자체로 또 다중비교 문제가 생긴다 — 학습 절반의 FDR 보정을 통과한 후보만 좁혀서
검증하면, 검증 단계의 가설 수가 작아 재검정이 훨씬 깨끗해진다(진짜 확증적 검증에
가까워짐).
"""

from __future__ import annotations

import sys
from pathlib import Path

from disclosure_lit.config import PROJECT_ROOT
from disclosure_lit.dictionary.holdout import (
    validate_significant_clusters,
)
from disclosure_lit.dictionary.outcome_labels import build_outcome_labels
from disclosure_lit.dictionary.stats import (
    OutcomeLabel,
)
from disclosure_lit.eval.console import fix_console_encoding

DEFAULT_RAW_DIR = PROJECT_ROOT / "data" / "raw"
DEFAULT_CACHE_DIR = PROJECT_ROOT / "data" / "cache"


def _build_labels(raw_dir: Path, cache_root: Path) -> tuple[list[str], list[OutcomeLabel]]:
    """held-out 검정에 필요한 재료(클러스터 ID 목록 + OutcomeLabel)를 준비한다.

    디스크 캐시(`cache_store`)에 같은 지문의 번들이 있으면 그대로 쓰고, 라벨이 비어
    있거나 번들이 없으면 새로 만든다 — `demo/app.py::_phrase_bundle()`은 2026-09-21
    부터 라벨 계산을 건너뛰고 빈 리스트를 저장하므로(그 docstring 참고, 화면이 그
    값을 안 읽어서 콜드스타트만 느려졌던 문제), 최근에 만들어진 번들에는 라벨이
    없을 수 있다. 이 평가는 라벨이 반드시 있어야 하니 그때는 여기서 채운다.

    라벨 계산은 `dictionary/outcome_labels.py::build_outcome_labels()`를 쓴다 —
    같은 매칭 로직을 여기 다시 구현하면 화면과 평가 두 경로가 갈라진다. 2026-09-24
    이전에는 이 함수를 `demo/app.py`에서 가져왔는데(L5 -> L4 역방향), 순환을 피하려고
    import를 함수 안에 숨겨둔 상태였다.
    """
    from disclosure_lit import cache_store
    from disclosure_lit.dictionary.candidates import (
        load_all_local_sections,
        select_clustering_pool,
    )
    from disclosure_lit.dictionary.stats import load_post_event_labels
    from disclosure_lit.retrieval.phrase_index import PhraseClusterIndex

    sections = load_all_local_sections(raw_dir)
    if not sections:
        print(f"로컬 코퍼스가 비어있습니다: {raw_dir}", file=sys.stderr)
        return [], []

    fingerprint = cache_store.phrase_fingerprint(sections, raw_dir)
    cached = cache_store.load_phrase_bundle(cache_root, fingerprint)
    if cached is not None:
        index, labels = cached
        print(f"디스크 캐시 재사용(지문 {fingerprint})", flush=True)
    else:
        print("캐시 없음 — 클러스터 인덱스를 새로 빌드합니다(수십 초~수 분)", flush=True)
        index = PhraseClusterIndex()
        pool, _ = select_clustering_pool(sections)
        index.build(pool)
        labels = []

    if not labels:
        print(
            "사후 이벤트 라벨을 계산합니다 — 코퍼스 전체 문구를 클러스터에 매칭하는"
            " 단계라 수 분~수십 분 걸립니다.",
            flush=True,
        )
        post_events = load_post_event_labels(raw_dir)
        if not post_events:
            print(
                f"사후 이벤트 라벨 파일이 없습니다: {raw_dir / 'post_event_labels.jsonl'}"
                " — scripts/label_post_events.py(A12)를 먼저 돌려야 합니다.",
                file=sys.stderr,
            )
            return [], []
        labels = build_outcome_labels(sections, post_events, index)
        # replace_existing=True가 꼭 필요하다: 캐시가 이미 있는데 라벨만 비어 있던
        # 경로(데모가 저장한 번들)로 들어온 경우, 교체하지 않으면 _atomic_write_dir()가
        # "이미 있으니 스킵"으로 조용히 돌아가 디스크 라벨은 계속 비어 있고 다음 실행
        # 때 이 수 분~수십 분짜리 매칭을 또 한다(PR #112 리뷰 @9abin 지적, 재현 확인).
        cache_store.save_phrase_bundle(
            cache_root, fingerprint, index, labels, replace_existing=True
        )
        print(f"라벨 {len(labels)}건 계산·캐시 저장 완료", flush=True)

    clusters, _centroids = index.dump()
    return [c.cluster_id for c in clusters], labels


def run(raw_dir: Path = DEFAULT_RAW_DIR, cache_root: Path = DEFAULT_CACHE_DIR) -> None:
    """held-out 검증을 실행하고 결과를 출력한다(오프라인 평가 진입점).

    `uv run python -m disclosure_lit.eval.l2b_holdout`로 돌린다 —
    `eval/run_eval.py`(L2a 팩트체크 평가)와 같은 패턴이다.

    **주의**: 이 수치는 `dictionary/stats.py`의 동점 처리가 결정적이어야 재현된다
    (2026-09-22 수정 이전에는 같은 데이터로도 실행마다 값이 달라졌다 — 이슈 #110).
    """
    # 첫 출력보다 먼저 불러야 한다 — 아래 진행 안내 문구에도 em dash가 들어가므로,
    # Windows cp949/strict stdout에서는 본작업 시작 전에 죽는다(PR #112 리뷰 @9abin).
    fix_console_encoding()
    cluster_ids, labels = _build_labels(raw_dir, cache_root)
    if not cluster_ids or not labels:
        return

    n_companies = len({label.corp_code for label in labels})
    print(
        f"\n클러스터 {len(cluster_ids)}개, 라벨 {len(labels)}건, 회사 {n_companies}개",
        flush=True,
    )

    checks = validate_significant_clusters(labels, cluster_ids)
    if not checks:
        print(
            "\n학습 절반에서 FDR 보정을 통과한 (클러스터, 사후이벤트) 조합이 0개입니다 —"
            " 검증할 대상 자체가 없다는 뜻이고 에러가 아닙니다(표본이 반으로 줄어 검정력 하락)."
        )
        return

    replicated = [c for c in checks if c.replicated]
    print(f"\n학습 절반 유의 조합 {len(checks)}개 → 검증 절반에서 재현 {len(replicated)}개")
    print(f"재현율: {len(replicated) / len(checks):.1%}")
    for c in checks:
        mark = "O" if c.replicated else "X"
        tr, ho = c.train_stats, c.holdout_stats
        print(
            f"  [{mark}] {c.cluster_id} / {c.outcome_label}: "
            f"train {tr.outcome_count}/{tr.sample_count}(기저 {tr.base_rate:.0%}, "
            f"z={tr.z_score:.1f}) → holdout {ho.outcome_count}/{ho.sample_count}"
            f"(z={ho.z_score:.1f})"
        )


if __name__ == "__main__":
    run()
