"""dictionary.cluster_meaning 단위 테스트. phrase_index/cluster처럼 가짜 임베딩으로
실제 모델 없이 클러스터링 동작을 검증한다.

섹션 스캔은 실제 `extract_phrase_candidates()`(candidates.py)를 그대로 쓰므로,
테스트 문구는 그 필터(길이>=8자, "다/함/음/임/됨." 로 끝나는 서술형 종결)를 실제로
통과하는 문장이어야 한다 — candidate 문구 자체를 그 문장 그대로로 맞춘다.
"""

from __future__ import annotations

import numpy as np
import pytest

import disclosure_lit.dictionary.cluster as cluster_module
import disclosure_lit.retrieval.phrase_index as phrase_index_module
from disclosure_lit.dart.ingest import DisclosureSection
from disclosure_lit.dictionary.cluster_meaning import (
    MIN_COMPANIES_FOR_MEANING,
    MIN_OCCURRENCES_FOR_MEANING,
    compute_cluster_meanings,
)
from disclosure_lit.retrieval.phrase_index import PhraseClusterIndex

_FAKE_VOCAB = ["재무구조", "성장동력", "무관"]

_PHRASE_A = "재무구조를 개선하였습니다."
_PHRASE_B = "재무구조를 건전화하였습니다."
_UNRELATED_PHRASE = "완전히 무관한 내용입니다."


def _fake_vector(text: str) -> np.ndarray:
    vec = np.array([1.0 if term in text else 0.0 for term in _FAKE_VOCAB], dtype=np.float32)
    norm = np.linalg.norm(vec)
    return vec / norm if norm > 0 else vec


def _fake_embed_texts(texts: list[str]) -> np.ndarray:
    return np.array([_fake_vector(t) for t in texts], dtype=np.float32)


@pytest.fixture(autouse=True)
def _mock_embeddings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cluster_module, "embed_texts", _fake_embed_texts)
    monkeypatch.setattr(phrase_index_module, "embed_texts", _fake_embed_texts)


def _section(corp_code: str, rcept_no: str, form_type: str, text: str) -> DisclosureSection:
    return DisclosureSection(
        rcept_no=rcept_no, corp_code=corp_code, form_type=form_type, section_title="", text=text
    )


class TestComputeClusterMeanings:
    def test_empty_index_returns_empty(self) -> None:
        index = PhraseClusterIndex()
        assert compute_cluster_meanings(index, [], []) == {}

    def test_pure_cluster_gets_reliable_meaning(self) -> None:
        """클러스터의 멤버 문구가 전부 같은 form_type 섹션에서만 나오고(순도 100%)
        회사 수도 충분하면(MIN_COMPANIES_FOR_MEANING 이상) 신뢰할 수 있는 의미로
        판정돼야 한다. 회사 수를 문턱보다 하나 더 넉넉히 줘서, 이 테스트가 순도만
        검증하고 회사 수 문턱 자체는 별도 테스트(아래)가 전담하게 한다."""
        candidates = [_PHRASE_A, _PHRASE_B]
        index = PhraseClusterIndex()
        index.build(candidates)

        n_companies = MIN_COMPANIES_FOR_MEANING + 1
        sections = [
            _section(f"C{i}", f"R{i}", "전환사채_발행", _PHRASE_A if i % 2 else _PHRASE_B)
            for i in range(n_companies)
        ]

        meanings = compute_cluster_meanings(index, candidates, sections)

        assert len(meanings) == 1
        meaning = next(iter(meanings.values()))
        assert meaning.dominant_form_type == "전환사채_발행"
        assert meaning.purity == 1.0
        assert meaning.company_count == n_companies
        assert meaning.is_reliable is True
        assert meaning.representative_phrase == _PHRASE_A

    def test_scattered_cluster_marked_unreliable(self) -> None:
        """같은 문구가 여러 form_type에 흩어져 나오면(회계 주석류) 순도가 낮아
        신뢰 불가로 판정돼야 한다(회사 수는 충분히 줘서 순도만으로 불신뢰가 되는지
        확인)."""
        candidates = [_PHRASE_A]
        index = PhraseClusterIndex()
        index.build(candidates)

        n_companies = MIN_COMPANIES_FOR_MEANING + 1
        form_types = ["전환사채_발행", "정기공시", "주주총회_소집"]
        sections = [
            _section(f"C{i}", f"R{i}", form_types[i % len(form_types)], _PHRASE_A)
            for i in range(n_companies)
        ]

        meanings = compute_cluster_meanings(index, candidates, sections)

        meaning = next(iter(meanings.values()))
        assert meaning.company_count == n_companies
        assert meaning.purity < 1.0
        assert meaning.is_reliable is False

    def test_low_company_count_marked_unreliable_despite_perfect_purity(self) -> None:
        """2026-09-21(팀 리뷰 지적) — 순도가 100%라도 서로 다른 회사 수가
        `MIN_COMPANIES_FOR_MEANING` 미만이면 "여러 회사가 흔히 쓰는 표현"이라는
        신뢰도 높은 주장은 하지 않는다. 매칭 자체는 여전히 성공한다(결과에 포함됨) —
        찾은 것과 신뢰할 만큼 흔한 것은 다른 질문이라는 게 이번 변경의 핵심.

        회사는 2곳뿐이지만(C1이 두 번 반복 제출) 발생 횟수는 MIN_OCCURRENCES_FOR_MEANING
        (3) 이상을 채워서, 이 테스트가 회사 수 문턱만 검증하고 발생 횟수 문턱과
        안 섞이게 한다."""
        candidates = [_PHRASE_A]
        index = PhraseClusterIndex()
        index.build(candidates)
        assert MIN_COMPANIES_FOR_MEANING > 2  # 이 테스트가 실제로 문턱 아래인지 전제 확인

        sections = [
            _section("C1", "R1", "전환사채_발행", _PHRASE_A),
            _section("C1", "R2", "전환사채_발행", _PHRASE_A),  # 같은 회사가 한 번 더
            _section("C2", "R3", "전환사채_발행", _PHRASE_A),
        ]

        meanings = compute_cluster_meanings(index, candidates, sections)

        assert len(meanings) == 1  # 매칭·분류 자체는 여전히 나온다
        meaning = next(iter(meanings.values()))
        assert meaning.purity == 1.0
        assert meaning.company_count == 2
        assert meaning.is_reliable is False  # 회사 수 미달이라 신뢰도만 낮다

    def test_below_min_occurrences_excluded(self) -> None:
        """MIN_OCCURRENCES_FOR_MEANING 미달이면 순도 계산 자체가 무의미해 결과에서
        빠져야 한다."""
        candidates = [_PHRASE_A]
        index = PhraseClusterIndex()
        index.build(candidates)
        assert MIN_OCCURRENCES_FOR_MEANING >= 2

        sections = [_section("C1", "R1", "전환사채_발행", _PHRASE_A)]

        meanings = compute_cluster_meanings(index, candidates, sections)

        assert meanings == {}

    def test_unmatched_phrases_do_not_crash(self) -> None:
        """섹션에 클러스터 후보가 아닌 문구만 있어도(매칭 실패) 에러 없이 빈 결과."""
        candidates = [_PHRASE_A]
        index = PhraseClusterIndex()
        index.build(candidates)

        sections = [_section("C1", "R1", "기타", _UNRELATED_PHRASE)]

        meanings = compute_cluster_meanings(index, candidates, sections)

        assert meanings == {}
