"""테스트 더블 모음 — 여러 테스트 파일이 함께 쓰는 가짜 객체.

`PhraseClusterIndex`는 실제로 쓰면 로컬 임베딩 모델이 필요해서, 매칭 로직만 검증할 땐
이 더블로 대신한다. 화면(`test_demo_app.py`)과 L2b 라벨 생성(`test_outcome_labels.py`)
두 곳에서 같은 더블이 필요해 여기로 모았다(2026-09-24 구조 감사 후속).
"""

from __future__ import annotations

from dataclasses import dataclass

from disclosure_lit.dart.ingest import DisclosureSection


@dataclass(frozen=True)
class _FakeCluster:
    cluster_id: str


class _FakeIndex:
    """`PhraseClusterIndex`의 테스트 더블 — `.match()`/`.match_many(phrases)`를 흉내 낸다.

    실제 `PhraseClusterIndex`(임베딩 필요)를 거치지 않고도 매칭 로직만 빠르게 단위
    테스트하기 위함. 문구 -> cluster_id 매핑을 그대로 흉내 낸다(없으면 매칭 실패로
    None). `whole_text_matches`는 `_match_cluster_for_text()`의 "전체 텍스트 우선
    매칭" 경로 테스트용 — 지정 안 하면 `.match()`는 항상 None(표 양식 공시처럼
    전체 매칭이 실패하는 경우를 기본값으로 삼는다).
    """

    def __init__(
        self, matches: dict[str, str], *, whole_text_matches: dict[str, str] | None = None
    ) -> None:
        self._matches = matches
        self._whole_text_matches = whole_text_matches or {}
        self.match_many_calls: list[list[str]] = []  # 배치 호출 1번인지 검증용

    def match(self, text: str) -> _FakeCluster | None:
        cluster_id = self._whole_text_matches.get(text)
        return _FakeCluster(cluster_id) if cluster_id is not None else None

    def match_many(self, phrases: list[str]) -> list[_FakeCluster | None]:
        self.match_many_calls.append(list(phrases))
        return [
            _FakeCluster(self._matches[p]) if self._matches.get(p) is not None else None
            for p in phrases
        ]


def _outcome_section(
    rcept_no: str, text: str, *, corp_code: str = "C1", form_type: str = "MOU"
) -> DisclosureSection:
    return DisclosureSection(
        rcept_no=rcept_no, corp_code=corp_code, form_type=form_type, section_title="제목", text=text
    )
