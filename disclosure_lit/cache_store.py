"""디스크 캐시 — 콜드스타트 단축용. 담당: A(이승노), 2026-09-17.

배경(실측, `demo/app.py`의 `@st.cache_resource` 함수들 콜드스타트 계측): `_company_index()`
40.6분 + `_outcome_labels()` 12.8분으로 부팅 1회에 총 54분이 걸린다 — 둘 다 프로세스
재시작(예: 데모 재배포)마다 매번 처음부터 다시 계산되는 인메모리 전용 캐시
(`@st.cache_resource`)라서다. 이 모듈은 그 결과를 디스크에 남겨 다음 부팅부터 재사용한다.

**설계의 핵심 제약(위험 요소 1번)**: `PhraseClusterIndex`가 만드는 `cluster_id`는 클러스터링에
쓴 phrase 집합에 의존한다 — 2026-09-21부터 알고리즘 자체는 입력 순서와 무관한 결정적
응집형 클러스터링이지만(`dictionary/cluster.py` 모듈 docstring), 코퍼스가 바뀌거나
후보 추출 로직이 바뀌면 phrase 집합 자체가 달라져 같은 표현이 다른 `cluster_id`를
받을 수 있다는 건 여전하다. 그 위에 `register_outcome_labels()`로 얹는 `OutcomeLabel`은
특정 `cluster_id` 값을 참조한다 — 클러스터 정의와 그 클러스터를 참조하는 라벨을
**따로** 캐싱/무효화하면, 서로 다른 시점에 빌드된 클러스터 ID가 섞여도 에러 없이
조용히 틀린 클러스터에 틀린 사후 이벤트 통계가 붙는다(silent corruption). 그래서
이 둘은 반드시 `phrase/` 캐시 번들 하나로만 쓰고 읽는다 — 부분 재사용 금지(2026-09-21
후속: `demo/app.py`가 이제 `OutcomeLabel`을 항상 빈 리스트로 채워 이 위험이 실질적으로
발동하진 않지만, 기능이 부활할 걸 대비해 배선 자체는 그대로 둔다).

`company/` 번들(`_company_index()`용)은 다른 데이터가 그 안의 ID를 참조하지 않아
독립적으로 캐싱해도 안전하다.

**무효화**: 캐시 디렉터리 이름 자체가 지문(fingerprint)이다 — 코퍼스(rcept_no 집합)·
관련 파라미터·임베딩 설정이 하나라도 바뀌면 다른 지문이 되어 자연스럽게 새 디렉터리에
쓰고, 예전 디렉터리는 정리한다(부분 무효화가 애초에 불가능한 구조).

**손상 내성**: 캐시가 없거나, 지문 디렉터리가 없거나, 파일이 손상되어 못 읽으면 항상
`None`을 반환해 호출부가 처음부터 재계산하게 한다 — 절대 부분적으로/추측으로 신뢰하지
않는다. 쓰기는 임시 디렉터리에 전부 쓴 뒤 존재하지 않는 목적지로의 단일 `os.replace`
(디렉터리 rename)로 옮긴다 — Windows에서는 이미 존재하는 디렉터리를 rename으로 덮어쓰는
게 지원되지 않으므로, 목적지가 이미 있으면(같은 지문 = 같은 내용) 아예 다시 쓰지 않고
건너뛴다.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import os
import shutil
import uuid
from collections.abc import Callable
from pathlib import Path

import numpy as np

from disclosure_lit import embed as embed_module
from disclosure_lit.config import settings
from disclosure_lit.dart.ingest import DisclosureSection
from disclosure_lit.dictionary.candidates import load_sections_jsonl
from disclosure_lit.dictionary.stats import OutcomeLabel
from disclosure_lit.retrieval.company_index import CompanyDisclosureIndex
from disclosure_lit.retrieval.phrase_index import PhraseCluster, PhraseClusterIndex

logger = logging.getLogger(__name__)

# 캐시 포맷이나 그 결과에 영향을 주는 로직(직렬화 스키마 등)이 바뀌면 올린다 — 지문에
# 섞여 들어가 구 버전 캐시를 자동으로 무효화한다.
_SCHEMA_VERSION = 1


# --- 지문(fingerprint) ---------------------------------------------------


def _sections_fingerprint(sections: list[DisclosureSection]) -> str:
    """rcept_no(+corp_code) 집합의 해시 — 코퍼스에 정확히 어떤 공시가 포함됐는지 식별한다.

    83000+건 섹션의 본문 전체를 매번 해시하면 콜드스타트만큼은 아니어도 비용이 커서,
    "어떤 공시가 포함됐는지"만 식별값으로 쓴다(추가/삭제는 잡아내고, 같은 rcept_no의
    본문 자체가 재청킹 등으로 바뀌는 경우는 `_SCHEMA_VERSION`이나 별도 파라미터
    지문으로 잡는다)."""
    ids = sorted(f"{s.rcept_no}:{s.corp_code}" for s in sections)
    return hashlib.sha256("\n".join(ids).encode("utf-8")).hexdigest()[:16]


def _post_events_fingerprint(raw_dir: Path) -> str:
    path = raw_dir / "post_event_labels.jsonl"
    if not path.exists():
        return "none"
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _embed_fingerprint_parts() -> list[str]:
    """임베딩 결과에 영향을 주는 설정 — 바뀌면 캐시된 벡터가 새 설정과 안 맞는다.

    `LOCAL_BATCH_SIZE`는 PR #76(2026-09-17 시점 미병합)에서 추가된 상수라 아직 이
    이름이 없는 `embed.py` 버전도 있다 — `getattr`로 없으면 빈 문자열로 둔다(배치
    크기는 배치 나누는 방식만 바꿀 뿐 결과 벡터 값 자체엔 영향이 없어서 지문에서
    빠져도 안전하다; 실제 벡터에 영향을 주는 `MAX_EMBED_CHARS`는 모든 버전에 있다).
    """
    return [
        settings.embed_backend,
        settings.local_embed_model,
        str(settings.embed_dim),
        str(embed_module.MAX_EMBED_CHARS),
        str(getattr(embed_module, "LOCAL_BATCH_SIZE", "")),
    ]


def company_fingerprint(sections: list[DisclosureSection]) -> str:
    parts = [str(_SCHEMA_VERSION), _sections_fingerprint(sections), *_embed_fingerprint_parts()]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


def phrase_fingerprint(sections: list[DisclosureSection], raw_dir: Path) -> str:
    from disclosure_lit.dictionary import candidates as candidates_module
    from disclosure_lit.dictionary.candidates import (
        CANDIDATE_EXTRACTION_VERSION,
        LEXICAL_MIN_COMPANIES,
        MAX_COMPANY_FRACTION,
        MIN_COMPANIES_DEFAULT,
    )
    from disclosure_lit.dictionary.cluster import CLUSTER_MIN_SCORE, CLUSTERING_VERSION

    parts = [
        str(_SCHEMA_VERSION),
        _sections_fingerprint(sections),
        _post_events_fingerprint(raw_dir),
        str(MIN_COMPANIES_DEFAULT),
        str(LEXICAL_MIN_COMPANIES),
        str(MAX_COMPANY_FRACTION),
        # 2026-09-22(상세 감사) — 목표 섹션 판별 자체를 바꾸는 두 상수. 빠져 있으면
        # 이 값들을 실측 근거로 조정할 때(예: TARGET_SECTION_PATTERN에 "정정" 추가)
        # CANDIDATE_EXTRACTION_VERSION 수동 bump를 깜빡하는 순간 이 지문이 이 파일이
        # 막으려는 바로 그 실패(튜닝 가능한 값이 지문에 안 잡혀 stale 캐시가 조용히
        # 재사용됨)를 그대로 재현한다.
        candidates_module.TARGET_SECTION_PATTERN.pattern,
        str(candidates_module._TARGET_SECTION_HEAD_CHARS),
        str(CLUSTER_MIN_SCORE),
        str(CANDIDATE_EXTRACTION_VERSION),
        str(CLUSTERING_VERSION),
        *_embed_fingerprint_parts(),
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


# --- 원자적 디렉터리 쓰기 ---------------------------------------------------


def _atomic_write_dir(final_dir: Path, write_contents: Callable[[Path], None]) -> None:
    """`final_dir`가 이미 있으면 아무것도 안 하고 돌아온다(같은 지문 = 같은 내용이라
    다시 쓸 필요가 없다). 없으면 임시 디렉터리에 전부 쓴 뒤 딱 한 번의 rename으로
    옮긴다 — 쓰는 도중 프로세스가 죽어도 `final_dir`엔 완전한 결과만 나타나거나 아예
    안 나타난다. 목적지가 존재하지 않는 rename은 POSIX·Windows 둘 다 원자적이다
    (반대로 이미 있는 디렉터리를 rename으로 "덮어쓰는" 동작은 Windows가 지원하지 않아,
    위에서 존재 여부로 먼저 걸러낸다)."""
    if final_dir.exists():
        return
    final_dir.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = final_dir.parent / f".tmp-{uuid.uuid4().hex}"
    tmp_dir.mkdir(parents=True)
    try:
        write_contents(tmp_dir)
        os.replace(tmp_dir, final_dir)
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise


def _atomic_replace_file(final_path: Path, write_contents: Callable[[Path], None]) -> None:
    """번들 **안의 파일 하나**를 제자리에서 원자적으로 교체한다.

    디렉터리와 달리 파일은 `os.replace()`가 이미 있는 목적지도 원자적으로 덮어쓴다
    (POSIX·Windows 공통). 그래서 "치워두고 → 옮기고 → 지우는" 다단계가 필요 없고,
    검사와 rename 사이에 다른 실행이 끼어들 창도 없다 — 읽는 쪽은 항상 교체 전 또는
    후의 완전한 파일만 본다.
    """
    tmp_path = final_path.parent / f".tmp-{uuid.uuid4().hex}-{final_path.name}"
    try:
        write_contents(tmp_path)
        os.replace(tmp_path, final_path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def _prune_old_generations(bundle_dir: Path, keep: str) -> None:
    """현재 지문(`keep`) 외의 예전 세대 디렉터리를 지운다(디스크 공간 회수, 실패해도
    안전에 영향 없는 정리 작업이라 실패를 무시한다)."""
    if not bundle_dir.exists():
        return
    for child in bundle_dir.iterdir():
        if child.is_dir() and child.name != keep and not child.name.startswith(".tmp-"):
            shutil.rmtree(child, ignore_errors=True)


def _write_jsonl(path: Path, rows: list) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(dataclasses.asdict(row), ensure_ascii=False) + "\n")


# --- company 번들 (독립 캐싱 가능) -------------------------------------------


def save_company_index(cache_root: Path, fingerprint: str, index: CompanyDisclosureIndex) -> None:
    bundle_dir = cache_root / "company"
    final_dir = bundle_dir / fingerprint
    sections, vectors = index.dump_rows()

    def _write(tmp_dir: Path) -> None:
        _write_jsonl(tmp_dir / "sections.jsonl", sections)
        np.save(tmp_dir / "vectors.npy", vectors)

    try:
        _atomic_write_dir(final_dir, _write)
    except Exception:
        logger.warning("company_index 캐시 쓰기 실패 — 캐시 없이 계속 진행", exc_info=True)
        return
    _prune_old_generations(bundle_dir, keep=fingerprint)


def load_company_index(cache_root: Path, fingerprint: str) -> CompanyDisclosureIndex | None:
    final_dir = cache_root / "company" / fingerprint
    if not final_dir.exists():
        return None
    try:
        sections = load_sections_jsonl(final_dir / "sections.jsonl")
        vectors = np.load(final_dir / "vectors.npy")
        index = CompanyDisclosureIndex()
        index.load_rows(sections, vectors)
        return index
    except Exception:
        logger.warning("company_index 캐시 로드 실패 — 처음부터 재계산", exc_info=True)
        return None


# --- company_lazy 번들 (회사별 지연 로딩, 2026-09-18) ------------------------
#
# 위 company_fingerprint()/save_company_index()/load_company_index()는 코퍼스
# 전체(100개사)를 한 번에 임베딩·캐싱한다 — 실측 40.6분. 실제 사용자는 한 번에
# 회사 하나만 조회하므로, `retrieval/company_index.py::CompanyDisclosureIndex`의
# `lazy_loader` 훅이 검색 시점에 그 회사만 이 번들로 채운다(부팅 시 전체를 미리
# 안 돈다). 회사 하나의 캐시가 다른 회사의 캐시를 참조하지 않으므로(위 company
# 번들과 같은 이유) 독립적으로 캐싱해도 안전하다 — 다만 저장 경로를 회사별로
# 나눠야(`company_lazy/<corp_code>/<지문>/`) 한 회사를 갱신할 때 다른 회사의
# `_prune_old_generations()`가 같이 지워지지 않는다.


def company_section_fingerprint(corp_code: str, sections: list[DisclosureSection]) -> str:
    """회사 하나의 지연 로딩 캐시 지문 — `company_fingerprint()`와 재료(임베딩
    설정)는 같지만 그 회사 자신의 섹션 집합만 본다."""
    parts = [
        str(_SCHEMA_VERSION),
        corp_code,
        _sections_fingerprint(sections),
        *_embed_fingerprint_parts(),
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


def save_company_section_vectors(
    cache_root: Path,
    corp_code: str,
    fingerprint: str,
    sections: list[DisclosureSection],
    vectors: np.ndarray,
) -> None:
    """회사 하나의 (섹션, 벡터)를 그 회사 전용 하위 디렉터리에 캐싱한다.

    `_prune_old_generations()`를 그 회사의 디렉터리 안에서만 돌려서, 한 회사를
    새로 캐싱해도 다른 회사의 지연 로딩 캐시는 건드리지 않는다."""
    bundle_dir = cache_root / "company_lazy" / corp_code
    final_dir = bundle_dir / fingerprint

    def _write(tmp_dir: Path) -> None:
        _write_jsonl(tmp_dir / "sections.jsonl", sections)
        np.save(tmp_dir / "vectors.npy", vectors)

    try:
        _atomic_write_dir(final_dir, _write)
    except Exception:
        logger.warning(
            "회사별 지연 로딩 캐시 쓰기 실패(corp_code=%s) — 캐시 없이 계속 진행",
            corp_code,
            exc_info=True,
        )
        return
    _prune_old_generations(bundle_dir, keep=fingerprint)


def load_company_section_vectors(
    cache_root: Path, corp_code: str, fingerprint: str
) -> tuple[list[DisclosureSection], np.ndarray] | None:
    final_dir = cache_root / "company_lazy" / corp_code / fingerprint
    if not final_dir.exists():
        return None
    try:
        sections = load_sections_jsonl(final_dir / "sections.jsonl")
        vectors = np.load(final_dir / "vectors.npy")
        return sections, vectors
    except Exception:
        logger.warning(
            "회사별 지연 로딩 캐시 로드 실패(corp_code=%s) — 처음부터 재계산",
            corp_code,
            exc_info=True,
        )
        return None


# --- phrase 번들 (phrase_index + outcome_labels, 절대 분리 캐싱 금지) -------------


def save_phrase_bundle(
    cache_root: Path,
    fingerprint: str,
    phrase_index: PhraseClusterIndex,
    outcome_labels: list[OutcomeLabel],
    *,
    replace_existing: bool = False,
) -> None:
    """클러스터 정의와 사후 이벤트 라벨을 하나의 지문 아래 함께 쓴다 — 모듈 docstring의
    cluster_id 비결정성 경고 참고. 절대 둘을 별개 호출로 나눠 쓰지 않는다.

    `replace_existing=True`는 **이미 있는 번들의 라벨을 채워 넣는** 한 가지 용도를 위한
    것이다. `demo/app.py`는 화면이 안 쓰는 라벨 계산을 건너뛰고 빈 리스트로 저장하는데
    (2026-09-21), 평가 러너는 그 라벨이 반드시 있어야 하므로 계산해서 채운다. 이때
    교체하지 않으면 매 평가 실행마다 수 분~수십 분짜리 매칭을 다시 한다.

    교체는 **디렉터리 통째가 아니라 라벨 파일 하나만** 제자리에서 바꾼다. 지문이 같으면
    클러스터링이 결정적이라(`dictionary/cluster.py`) clusters/centroids는 이미 같은
    내용이므로 다시 쓸 이유가 없고, 무엇보다 디렉터리를 치웠다 옮기는 방식은 그 사이
    다른 실행이 끼어들 창을 만든다 — 실제로 2026-09-22 리뷰(@9abin)에서 데모의 빈 라벨
    저장이 평가가 막 완성한 라벨을 지우는 경쟁 상태로 재현됐다. 파일 단위 `os.replace()`는
    원자적이라 그 창 자체가 없다."""
    bundle_dir = cache_root / "phrase"
    final_dir = bundle_dir / fingerprint
    clusters, centroids = phrase_index.dump()
    # 2026-09-21: 정확한 멤버십(문구 → cluster_id)도 같이 남긴다 — 없으면 로드 후
    # cluster_meaning이 match_many()로 근사 복원해야 해서(전체 풀 재임베딩, 수 분)
    # 느리고, 진짜 centroid 이전엔 그 근사가 4.5% 틀렸다. 선택 파일이라 없어도 로드된다.
    members = phrase_index.member_cluster_ids()

    def _write(tmp_dir: Path) -> None:
        _write_jsonl(tmp_dir / "clusters.jsonl", clusters)
        np.save(tmp_dir / "centroids.npy", centroids)
        _write_jsonl(tmp_dir / "outcome_labels.jsonl", outcome_labels)
        if members is not None:
            (tmp_dir / "members.json").write_text(
                json.dumps(members, ensure_ascii=False), encoding="utf-8"
            )

    try:
        if replace_existing and final_dir.exists():
            if not outcome_labels:
                # "보충"은 채우는 동작이지 비우는 동작이 아니다. 빈 라벨로 교체를
                # 요청받으면 아무것도 하지 않는다 — 이 가드가 없으면 라벨 계산에
                # 실패한 실행이 멀쩡한 라벨 캐시를 지워버릴 수 있다.
                return
            # members.json은 없을 때만 채운다 — 같은 지문이면 내용이 같으므로 다시 쓸
            # 이유가 없고, 2026-09-21 이전 번들에는 이 파일 자체가 없어서 보충이 필요하다.
            if members is not None and not (final_dir / "members.json").exists():
                _atomic_replace_file(
                    final_dir / "members.json",
                    lambda p: p.write_text(
                        json.dumps(members, ensure_ascii=False), encoding="utf-8"
                    ),
                )
            # 라벨을 마지막에 쓴다 — 중간에 죽어도 라벨은 교체 전 상태로 온전히 남고,
            # 다음 실행이 같은 보충을 다시 시도하면 그만이다.
            _atomic_replace_file(
                final_dir / "outcome_labels.jsonl",
                lambda p: _write_jsonl(p, outcome_labels),
            )
        else:
            _atomic_write_dir(final_dir, _write)
    except Exception:
        logger.warning(
            "phrase_index+outcome_labels 캐시 쓰기 실패 — 캐시 없이 계속 진행", exc_info=True
        )
        return
    _prune_old_generations(bundle_dir, keep=fingerprint)


def load_phrase_bundle(
    cache_root: Path, fingerprint: str
) -> tuple[PhraseClusterIndex, list[OutcomeLabel]] | None:
    """번들 전체(클러스터+라벨)를 함께 반환한다 — 절대 절반만 반환하지 않는다.

    둘 중 하나라도 읽기 실패하면 `None`을 돌려줘서 호출부가 둘 다 처음부터
    재계산하게 한다(원자성 보장, 모듈 docstring 참고) — 예를 들어 `clusters.jsonl`은
    멀쩡한데 `outcome_labels.jsonl`만 깨졌다고 클러스터만 재사용하면, 재계산되는 새
    라벨이 아니라 옛 지문의 라벨이 섞여 들어갈 위험이 남는다."""
    final_dir = cache_root / "phrase" / fingerprint
    if not final_dir.exists():
        return None
    try:
        clusters: list[PhraseCluster] = []
        with (final_dir / "clusters.jsonl").open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    clusters.append(PhraseCluster(**json.loads(line)))
        centroids = np.load(final_dir / "centroids.npy")

        labels: list[OutcomeLabel] = []
        with (final_dir / "outcome_labels.jsonl").open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    labels.append(OutcomeLabel(**json.loads(line)))

        members_path = final_dir / "members.json"
        members: dict[str, str] | None = (
            json.loads(members_path.read_text(encoding="utf-8")) if members_path.exists() else None
        )

        index = PhraseClusterIndex()
        index.load(clusters, centroids, cluster_id_by_phrase=members)
        return index, labels
    except Exception:
        logger.warning(
            "phrase_index+outcome_labels 캐시 로드 실패 — 처음부터 재계산", exc_info=True
        )
        return None


# --- cluster_meanings 캐시 ---------------------------------------------------
#
# 2026-09-25 추가. `demo/app.py::_cluster_meanings()`는 "82,876섹션 기준 실측 수십 초라
# 무시할 수준"이라는 근거로 디스크 캐싱을 하지 않았는데, 응답시간 실측
# (`docs/testing/response-time.md`)에서 **서버 기동마다 약 4분 20초**가 드는 것으로
# 나왔다 — 캐시되는 클러스터 번들 생성보다도 길다. 전제가 깨졌으므로 캐시한다.
#
# 파일 하나(JSON)라 디렉터리 번들과 달리 `_atomic_replace_file`로 제자리 교체한다
# (DEC-032와 같은 이유 — 디렉터리를 치우고 옮기는 다단계는 경쟁 상태를 만든다).


def cluster_meanings_fingerprint(sections: list[DisclosureSection], raw_dir: Path) -> str:
    """클러스터 번들 지문 + "의미" 계산에만 쓰이는 튜닝값을 합친 지문.

    번들 지문만 쓰면, 순도 문턱 같은 값을 조정했을 때 예전 결과가 조용히 재사용된다 —
    `phrase_fingerprint`가 클러스터링 상수를 지문에 넣는 것과 같은 이유다. 상수는
    모듈 속성으로 그때그때 읽는다(테스트가 값을 바꿔 끼울 수 있어야 한다).
    """
    from disclosure_lit.dictionary import cluster_meaning as meaning_module

    parts = [
        str(_SCHEMA_VERSION),
        phrase_fingerprint(sections, raw_dir),
        str(meaning_module.MIN_PURITY_TO_DISPLAY),
        str(meaning_module.MIN_OCCURRENCES_FOR_MEANING),
        str(meaning_module.MIN_COMPANIES_FOR_MEANING),
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


def _cluster_meanings_path(cache_root: Path, fingerprint: str) -> Path:
    return cache_root / "cluster_meanings" / f"{fingerprint}.json"


def save_cluster_meanings(cache_root: Path, fingerprint: str, meanings: dict) -> None:
    """계산 결과를 지문별 JSON 파일로 남긴다. 실패해도 조회 자체는 계속돼야 한다."""
    path = _cluster_meanings_path(cache_root, fingerprint)
    path.parent.mkdir(parents=True, exist_ok=True)

    def _write(tmp_path: Path) -> None:
        rows = {cid: dataclasses.asdict(meaning) for cid, meaning in meanings.items()}
        tmp_path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")

    try:
        _atomic_replace_file(path, _write)
    except Exception:
        logger.warning("cluster_meanings 캐시 저장 실패 — 다음 기동에 재계산", exc_info=True)
        return

    # 예전 지문 파일 정리(실패해도 안전에 영향 없음).
    for child in path.parent.glob("*.json"):
        if child.name != path.name:
            child.unlink(missing_ok=True)


def load_cluster_meanings(cache_root: Path, fingerprint: str) -> dict | None:
    """저장된 결과를 돌려준다. 없거나 깨졌으면 `None`(호출부가 재계산).

    **빈 dict와 `None`은 다르다** — 코퍼스가 비어 결과가 없는 경우까지 매번 4분씩
    재계산하지 않도록, 저장된 빈 결과는 빈 dict 그대로 돌려준다.
    """
    from disclosure_lit.dictionary.cluster_meaning import ClusterMeaning

    path = _cluster_meanings_path(cache_root, fingerprint)
    if not path.exists():
        return None
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
        return {cid: ClusterMeaning(**fields) for cid, fields in rows.items()}
    except Exception:
        logger.warning("cluster_meanings 캐시 로드 실패 — 처음부터 재계산", exc_info=True)
        return None
