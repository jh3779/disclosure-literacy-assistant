"""A7 — CompanyDisclosureIndex 단위 테스트. 키워드+임베딩 하이브리드 검색.

임베딩은 결정론적인 가짜 벡터로 모킹한다(httpx 를 모킹하는 다른 테스트들과 같은 원칙) —
실제 dragonkue/BGE-m3-ko 모델은 무겁고(sentence-transformers/torch, ~2.5GB) CI에는
optional extra(`local`)로만 설치되므로, 기본 테스트가 이를 요구하면 안 된다
(`.github/workflows/ci.yml`의 "local extra는 설치 안 함" 방침). 진짜 모델로 의미적
유사도까지 검증하는 테스트는 tests/test_embed.py 의 `slow` 마커 테스트가 담당한다.
"""

from __future__ import annotations

import faiss
import numpy as np
import pytest

import disclosure_lit.retrieval.company_index as company_index_module
from disclosure_lit.dart.ingest import DisclosureSection
from disclosure_lit.retrieval.company_index import (
    DEFAULT_MIN_SCORE,
    LARGE_SECTION_PENALTY,
    CompanyDisclosureIndex,
    _keyword_bonus_for,
)

# 실제 모델 대신 쓰는 결정론적 가짜 임베딩 — 이 어휘 중 텍스트에 포함된 것만 1로 표시하는
# one-hot 벡터를 정규화한다. 진짜 의미 유사도를 흉내내진 못하지만("겹치는 용어가 많을수록
# 유사도가 높다"만 재현), 하이브리드 스코어링/랭킹 로직 자체를 결정론적으로 검증하기엔
# 충분하다.
_FAKE_VOCAB = ["유상증자", "자기주식", "배당", "합병", "mou"]


def _fake_vector(text: str) -> np.ndarray:
    lowered = text.lower()
    vec = np.array([1.0 if term in lowered else 0.0 for term in _FAKE_VOCAB], dtype=np.float32)
    norm = np.linalg.norm(vec)
    return vec / norm if norm > 0 else vec


def _fake_embed_texts(texts: list[str]) -> np.ndarray:
    return np.array([_fake_vector(t) for t in texts], dtype=np.float32)


def _fake_embed_query(text: str) -> np.ndarray:
    return _fake_vector(text)


@pytest.fixture(autouse=True)
def _mock_embeddings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(company_index_module, "embed_texts", _fake_embed_texts)
    monkeypatch.setattr(company_index_module, "embed_query", _fake_embed_query)


def _section(
    corp_code: str, rcept_no: str, title: str, text: str, *, rcept_dt: str = ""
) -> DisclosureSection:
    return DisclosureSection(
        rcept_no=rcept_no,
        corp_code=corp_code,
        form_type="유상증자",
        section_title=title,
        text=text,
        rcept_dt=rcept_dt,
    )


class TestIndex:
    def test_scored_search_preserves_order_and_hybrid_score(self):
        index = CompanyDisclosureIndex()
        sections = [
            _section("C1", "R1", "유상증자", "유상증자"),
            _section("C1", "R2", "배당", "배당"),
        ]
        index.index(sections)
        hits = index.search_with_scores("C1", "유상증자", min_score=float("-inf"))
        assert [hit.section for hit in hits] == index.search(
            "C1", "유상증자", min_score=float("-inf")
        )
        assert hits[0].score == pytest.approx(2.0)
        assert hits[1].score == pytest.approx(0.0)
        assert all(hit.score is None for hit in index.search_with_scores("C1", ""))

    def test_groups_sections_by_corp_code(self) -> None:
        index = CompanyDisclosureIndex()
        index.index(
            [
                _section("C1", "R1", "유상증자 결정", "본문1"),
                _section("C2", "R2", "자기주식 취득", "본문2"),
            ]
        )

        assert index.search("C1", "") == [_section("C1", "R1", "유상증자 결정", "본문1")]
        assert index.search("C2", "") == [_section("C2", "R2", "자기주식 취득", "본문2")]

    def test_multiple_index_calls_accumulate(self) -> None:
        index = CompanyDisclosureIndex()
        index.index([_section("C1", "R1", "제목1", "본문1")])
        index.index([_section("C1", "R2", "제목2", "본문2")])

        result = index.search("C1", "")
        assert [s.rcept_no for s in result] == ["R1", "R2"]

    def test_unknown_corp_code_returns_empty(self) -> None:
        index = CompanyDisclosureIndex()
        index.index([_section("C1", "R1", "제목", "본문")])
        assert index.search("없는회사", "") == []

    def test_duplicate_rcept_no_and_title_is_skipped_and_logs_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Issue #80 — 같은 (rcept_no, section_title) 섹션이 재인덱싱 등으로 두 번
        들어와도 중복 적재되면 안 된다(검색 결과 중복·코사인 랭킹 왜곡 방지)."""
        index = CompanyDisclosureIndex()
        index.index([_section("C1", "R1", "제목1", "본문1")])

        with caplog.at_level("WARNING"):
            index.index([_section("C1", "R1", "제목1", "본문1(다시 들어옴)")])

        result = index.search("C1", "")
        assert [s.rcept_no for s in result] == ["R1"]  # 중복 적재 안 됨
        assert any("중복 섹션 무시" in record.getMessage() for record in caplog.records)

    def test_same_rcept_no_different_title_is_not_treated_as_duplicate(self) -> None:
        """한 rcept_no가 여러 섹션(서로 다른 section_title)을 낼 수 있으므로,
        rcept_no만으로 중복 판정하면 안 된다."""
        index = CompanyDisclosureIndex()
        index.index([_section("C1", "R1", "제목A", "본문A")])
        index.index([_section("C1", "R1", "제목B", "본문B")])

        result = index.search("C1", "")
        assert {s.section_title for s in result} == {"제목A", "제목B"}


class TestSearch:
    def test_keyword_matches_title_or_text(self) -> None:
        index = CompanyDisclosureIndex()
        index.index(
            [
                _section("C1", "R1", "유상증자 결정", "신주 발행"),
                _section("C1", "R2", "자기주식 취득", "유상증자와 무관"),
                _section("C1", "R3", "배당 결정", "현금 배당"),
            ]
        )

        result = index.search("C1", "유상증자")
        assert {s.rcept_no for s in result} == {"R1", "R2"}

    def test_keyword_match_always_ranks_above_pure_semantic_match(self) -> None:
        """키워드가 정확히 포함된 섹션은 +1.0 보너스 덕에, 키워드는 없지만 의미적으로만
        겹치는 섹션보다 항상 위에 랭크돼야 한다."""
        index = CompanyDisclosureIndex()
        index.index(
            [
                _section("C1", "R_exact", "유상증자 결정", "본문"),
                # "자기주식"만 겹쳐서 순수 코사인 유사도만 갖는다(키워드 매치 없음)
                _section("C1", "R_semantic_only", "자기주식 취득", "본문"),
            ]
        )

        result = index.search("C1", "자기주식")
        rcept_nos = [s.rcept_no for s in result]
        assert rcept_nos[0] == "R_semantic_only"  # "자기주식" 자체가 키워드라 매치됨

    def test_multiword_keyword_scattered_across_section_still_gets_partial_bonus(self) -> None:
        """이슈 #105 — 실측(구영테크, corp_code 00202060): 실제 공시 텍스트는 "전환사채
        (해외전환사채 포함) 발행후..."처럼 검색어 단어 사이에 다른 문구가 끼어 있어,
        "전환사채 발행"이라는 연속 문자열이 그대로 나오는 경우가 드물다 — 그러면 옛
        구현은 keyword_bonus가 0이 돼 순수 코사인 유사도만으로 순위가 갈렸고, 흔한
        단어("발행")의 유사도가 희귀한 단어("유상증자")의 신호를 덮어써 완전히 무관한
        섹션이 위로 올라왔다. 두 단어가 흩어져서라도 다 있으면 부분 보너스를 받아야
        한다."""
        index = CompanyDisclosureIndex()
        index.index(
            [
                # "유상증자 발행"이 연속 문자열로는 없지만 두 단어 다 흩어져서 있다.
                _section("C1", "R_scattered", "유상증자 결정", "신주를 조만간 발행할 예정"),
                # 키워드 단어는 하나도 없고 가짜 임베딩 어휘("자기주식")만 겹친다.
                _section("C1", "R_semantic_only", "자기주식 취득", "본문"),
            ]
        )

        result = index.search("C1", "유상증자 발행")

        assert result[0].rcept_no == "R_scattered"

    def test_multiword_keyword_zero_words_matched_gets_no_bonus(self) -> None:
        index = CompanyDisclosureIndex()
        index.index([_section("C1", "R1", "배당 결정", "현금 배당 지급")])

        # "유상증자 합병" 어느 단어도 섹션에 없다 — 보너스 0, 순수 코사인만으로 판정.
        # 가짜 임베딩 어휘에 "유상증자"/"합병" 둘 다 있어 코사인 유사도 자체는 0이 아니지만
        # (겹치는 항이 없으므로 실제로는 0), 어쨌든 min_score(기본 0.3) 밑이라 빈 리스트.
        assert index.search("C1", "유상증자 합병") == []

    def test_multiword_exact_contiguous_match_still_gets_full_bonus(self) -> None:
        """전체 문자열이 그대로 들어있으면(기존 동작) 부분 매치보다 항상 우선한다 —
        "정확히 일치하면 반드시 반환" 보장은 이번 변경으로도 그대로 유지된다."""
        index = CompanyDisclosureIndex()
        index.index(
            [
                _section("C1", "R_exact", "본문", "유상증자 결정을 공시합니다"),
                # 단어는 둘 다 있지만 흩어져 있어 부분 보너스만 받는다.
                _section("C1", "R_scattered", "본문", "유상증자는 아니고 결정된 바 없음"),
            ]
        )

        result = index.search("C1", "유상증자 결정")

        assert result[0].rcept_no == "R_exact"

    def test_exact_phrase_still_wins_when_scattered_candidate_has_a_later_date(
        self,
    ) -> None:
        """R106-1 (2026-09-21 외부 리뷰, 독립 재현으로 확인) — 위 테스트는 정확 구문을
        먼저 넣고 두 섹션의 날짜도 같게 둬서, 실제로는 두 후보의 키워드 보너스가
        완전히 동점(1.0 vs 1.0)인 채로 `search()` 내부 정렬이 유지한 입력 순서만으로
        통과한다(정확 구문 우선순위가 점수에 실제로 반영된 게 아니다). 여기서는
        흩어진 단어 후보의 날짜를 더 최신으로 둔다 — 현재 코드는 동점 시 날짜 역순
        정렬이 최종 승자를 가르므로(company_index.py의 `(scores[i], rcept_dt)` 정렬
        키), 정확 구문 우선순위가 점수 자체에 없으면 이 케이스에서 흩어진 단어 쪽이
        이겨버린다."""
        index = CompanyDisclosureIndex()
        index.index(
            [
                _section(
                    "C1", "R_exact", "본문", "유상증자 결정을 공시합니다", rcept_dt="20260901"
                ),
                _section(
                    "C1",
                    "R_scattered",
                    "본문",
                    "유상증자는 아니고 결정된 바 없음",
                    rcept_dt="20260902",
                ),
            ]
        )

        result = index.search("C1", "유상증자 결정")

        assert result[0].rcept_no == "R_exact"

    def test_single_common_word_alone_cannot_cross_relevance_threshold(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """R106-2 (2026-09-21 외부 리뷰, 독립 재현으로 확인) — 의미적으로 무관한
        문서라도 다단어 검색어 중 흔한 단어 하나만 겹치면 `matched/len(needle_words)`
        보너스(여기서는 1/2=0.5)만으로 기본 문턱(0.3)을 그냥 넘는다. 이 테스트는
        파일 상단의 공유 가짜 어휘 대신 임베딩 유사도를 0으로 직접 통제해(리뷰의
        재현 방식과 동일) 키워드 보너스만 분리해서 검증한다 — 핵심어 없이 흔한
        단어 하나만 일치하는 경우는 통과하면 안 된다."""
        monkeypatch.setattr(
            company_index_module,
            "embed_texts",
            lambda texts: np.tile(np.array([[1.0, 0.0]], dtype=np.float32), (len(texts), 1)),
        )
        monkeypatch.setattr(
            company_index_module, "embed_query", lambda _: np.array([0.0, 1.0], dtype=np.float32)
        )
        index = CompanyDisclosureIndex()
        index.index(
            [
                # "발행"만 겹치고 "전환사채"는 없는, 실제로는 무관한 문서.
                _section("C1", "R_unrelated", "본문", "제품 설명서를 발행하였습니다"),
            ]
        )

        result = index.search("C1", "전환사채 발행")

        assert result == []

    def test_keyword_search_is_case_insensitive(self) -> None:
        index = CompanyDisclosureIndex()
        index.index([_section("C1", "R1", "MOU 체결", "본문")])

        assert index.search("C1", "mou") != []
        assert index.search("C1", "MOU") != []

    def test_keyword_restricted_to_given_corp_code(self) -> None:
        index = CompanyDisclosureIndex()
        index.index(
            [
                _section("C1", "R1", "유상증자 결정", "본문"),
                _section("C2", "R2", "유상증자 결정", "본문"),
            ]
        )

        result = index.search("C1", "유상증자")
        assert [s.rcept_no for s in result] == ["R1"]

    def test_top_k_limits_result_count(self) -> None:
        index = CompanyDisclosureIndex()
        index.index([_section("C1", f"R{i}", "유상증자", "본문") for i in range(5)])

        assert len(index.search("C1", "유상증자", top_k=2)) == 2
        assert len(index.search("C1", "", top_k=3)) == 3

    def test_no_match_returns_empty_list(self) -> None:
        index = CompanyDisclosureIndex()
        index.index([_section("C1", "R1", "유상증자 결정", "본문")])
        assert index.search("C1", "존재하지않는키워드") == []

    def test_min_score_threshold_filters_weak_semantic_matches(self) -> None:
        """키워드가 없고 임베딩 유사도도 낮으면(문턱 미만) 결과에서 제외돼야 한다 —
        임베딩만으로 항상 top_k 를 채우면 무관한 결과까지 순위만 매겨 반환하게 된다."""
        index = CompanyDisclosureIndex()
        index.index([_section("C1", "R1", "합병 결정", "본문")])

        # "배당"과 "합병"은 가짜 어휘에서 서로 다른 차원이라 코사인 유사도가 0이다
        result = index.search("C1", "배당", min_score=0.3)
        assert result == []

    def test_vector_section_count_mismatch_logs_warning_and_falls_back_to_keyword_only(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Issue #80 — 벡터 개수와 섹션 개수가 어긋나면(내부 상태 손상) 의미 검색이
        조용히 0점 처리되던 걸 경고 로그로 드러나게 한다. 키워드 매칭 자체는
        (기존 동작대로) 계속 동작해야 한다."""
        index = CompanyDisclosureIndex()
        index.index([_section("C1", "R1", "유상증자 결정", "본문")])
        # 내부 상태를 인위적으로 손상시켜 벡터 개수와 섹션 개수를 어긋나게 만든다
        # (빈 FAISS 인덱스로 교체 — ntotal=0 vs 섹션 1개).
        index._faiss_by_corp["C1"] = faiss.IndexFlatIP(len(_FAKE_VOCAB))

        with caplog.at_level("WARNING"):
            result = index.search("C1", "유상증자")

        assert [s.rcept_no for s in result] == ["R1"]  # 키워드 보너스만으로도 반환됨
        assert any("내부 상태 불일치" in record.getMessage() for record in caplog.records)
        assert any("C1" in record.getMessage() for record in caplog.records)


class TestLargeSectionPenalty:
    """이슈 #75 후속 — 재무제표류 거대 섹션(정기공시 "사업의 내용" 등, 실측 최대
    150만자)이 임베딩 유사도만으로 top_k 슬롯을 차지해 더 짧고 실제로 관련 있는
    서사형 섹션을 밀어내는 걸 막는다. B가 PR #77에서 verify_claim 프롬프트 렌더링
    단계에 건 길이 상한(방어선)과 짝을 이루는 근본 처방(순위 하향)이다."""

    def _huge_text(self, content: str) -> str:
        """LARGE_SECTION_CHARS(20,000자)를 넘는 텍스트 — 가짜 어휘와 겹치지 않는
        필러로 채운 뒤 실제 내용을 붙인다."""
        return ("가" * 20_001) + content

    def test_huge_section_with_exact_keyword_still_returned_but_ranked_lower(self) -> None:
        """정확히 키워드가 포함된 거대 섹션은 감점을 얹어도(1.0 보너스가 0.5 감점을
        상쇄하고 남음) min_score를 넘어 반환은 되지만, 같은 매치의 일반 섹션보다
        아래로 랭크돼야 한다 — 완전히 잃지는 않되(recall 유지) 우선순위만 밀림."""
        index = CompanyDisclosureIndex()
        index.index(
            [
                _section("C1", "R_normal", "합병 결정", "합병 결정 공시"),
                _section("C1", "R_huge", "합병 결정(거대)", self._huge_text("합병 결정 공시")),
            ]
        )

        result = index.search("C1", "합병")
        rcept_nos = [s.rcept_no for s in result]
        assert set(rcept_nos) == {"R_normal", "R_huge"}  # 둘 다 반환됨(recall 유지)
        assert rcept_nos[0] == "R_normal"  # 감점 없는 쪽이 위로 랭크

    def test_huge_section_without_exact_keyword_dropped_below_threshold(self) -> None:
        """거대 섹션은 감점이 크면 min_score 밑으로 떨어져 결과에서 제외돼야 한다.

        쿼리를 4단어("유상증자 합병 배당 mou")로 섞어 그중 "합병" 하나만 섹션에
        있게 한다 — 겹침 비율 1/4=0.25는 과반(R106-2 수정, `MIN_KEYWORD_OVERLAP_
        FRACTION=0.5`) 이하라 키워드 보너스는 0이다. 코사인은 가짜 어휘 중 "합병"
        차원만 겹쳐 1/sqrt(4) = 0.5(쿼리 벡터는 4개 항 모두 1이라 크기 2, 문서
        벡터는 "합병"만 1). 거대 섹션: 0.5 + 0 - 0.5(감점) = 0.0 < 0.3 → 제외.
        짧은 섹션: 0.5 + 0 - 0 = 0.5 ≥ 0.3 → 포함."""
        index = CompanyDisclosureIndex()
        index.index(
            [
                _section("C1", "R_short", "합병 관련 공시", "합병 결정 공시"),
                _section("C1", "R_huge", "합병 관련 공시(거대)", self._huge_text("합병 결정 공시")),
            ]
        )

        result = index.search("C1", "유상증자 합병 배당 mou", min_score=0.3)
        rcept_nos = [s.rcept_no for s in result]
        assert rcept_nos == ["R_short"]  # 짧은 쪽만 살아남음
        assert "R_huge" not in rcept_nos  # 감점으로 min_score 밑으로 떨어져 제외됨

    def test_section_at_exact_length_boundary_is_not_penalized(self) -> None:
        """LARGE_SECTION_CHARS(20,000자) 그 자체는 초과가 아니라 감점 대상이 아니다 —
        20,000자 섹션과 20,001자 섹션을 직접 비교해 경계에서만 감점이 붙는지 확인한다."""
        index = CompanyDisclosureIndex()
        at_boundary = "합병" + "가" * (20_000 - len("합병"))
        over_boundary = at_boundary + "가"
        assert len(at_boundary) == 20_000
        assert len(over_boundary) == 20_001
        index.index(
            [
                _section("C1", "R_at", "합병 결정(경계)", at_boundary),
                _section("C1", "R_over", "합병 결정(경계초과)", over_boundary),
            ]
        )

        result = index.search("C1", "합병")
        rcept_nos = [s.rcept_no for s in result]
        assert set(rcept_nos) == {"R_at", "R_over"}  # 둘 다 반환(recall 유지)
        assert rcept_nos[0] == "R_at"  # 감점 없는 쪽(정확히 20,000자)이 위로 랭크

    def test_partial_keyword_match_alone_can_never_offset_large_section_penalty(
        self,
    ) -> None:
        """2026-09-22 감사(상세 감사, review-agent) — 처음엔 `PARTIAL_KEYWORD_MATCH_
        CAP=0.6`이 `LARGE_SECTION_PENALTY=0.5`보다 커서, 2단어 쿼리의 두 단어가
        거대 섹션에 흩어진 채로만 있어도(연속 문구는 없음) 부분 보너스가 감점을
        순이익 +0.1로 넘어섰다 — 이슈 #75가 막으려던 "거대 섹션이 애매한 신호만으로
        top_k를 차지"하는 문제가 다단어 쿼리로 재발할 수 있었다(실측 재현: 코사인
        0.27만 더해도 0.37로 min_score(0.3)를 넘음). `claim.keyword`가 5~15자
        짧은 구(`factcheck/claim.py`)라 2단어 쿼리가 예외가 아니라 주류라서 실사용
        재발 가능성이 낮지 않았다. `_keyword_bonus_for()`를 직접 호출해, 부분 일치
        보너스만으로는(코사인 기여 없이) 거대 섹션 감점을 절대 상쇄 못 한다는 불변식을
        검증한다 — 정확 구문(1.0)은 여전히 감점을 넘어야 하므로 함께 확인한다."""
        huge_text = self._huge_text("유상증자 결정 이후 합병 논의가 있었습니다")
        section = _section("C1", "R_huge", "본문", huge_text)

        scattered_bonus = _keyword_bonus_for(section, "유상증자 합병", ["유상증자", "합병"])
        assert scattered_bonus < LARGE_SECTION_PENALTY

        exact_bonus = _keyword_bonus_for(section, "유상증자 결정", ["유상증자", "결정"])
        assert exact_bonus - LARGE_SECTION_PENALTY > 0  # 정확 구문은 여전히 감점을 넘음


class TestScoringConstantInvariants:
    """2026-09-22 팀 리뷰(@9abin) 지적 — 스코어링 상수 불변식 가드가 `assert`
    문이라 `python -O`에서 통째로 사라지는 문제, 그리고 부분 일치 보너스가
    코사인 기여 없이도 관련성 문턱을 단독으로 넘을 수 있던 문제."""

    def test_invariant_guard_survives_python_optimize_flag(self) -> None:
        """`assert` 대신 일반 함수 호출로 바꿨으니, `__debug__=False`(즉 `-O`로
        실행한 것과 동일한 상황)를 흉내 내도 불변식 위반을 여전히 잡아야 한다.
        `-O` 자체를 서브프로세스로 재현하는 대신, 상수를 깨뜨린 뒤 검증 함수를
        직접 호출해 "함수 호출"이라는 메커니즘 자체가 `-O`에 영향받지 않음을
        보인다(assert 문이었다면 이 호출 지점 자체가 -O 빌드에서 사라졌을 것)."""
        original = company_index_module.PARTIAL_KEYWORD_MATCH_CAP
        try:
            company_index_module.PARTIAL_KEYWORD_MATCH_CAP = 0.6  # LARGE_SECTION_PENALTY 이상
            with pytest.raises(AssertionError):
                company_index_module._validate_scoring_constants()
        finally:
            company_index_module.PARTIAL_KEYWORD_MATCH_CAP = original

    def test_partial_bonus_alone_cannot_cross_relevance_threshold_without_cosine(
        self,
    ) -> None:
        """회귀 테스트(팀 리뷰 @9abin 반례) — 검색어 "증자 발행"이 의미상 무관한
        문서("무상증자"+"발행어음", 단어 조각만 우연히 겹침)에서 fraction=1.0으로
        잡혀도, 코사인 유사도가 0이면 부분 보너스만으로는 관련성 문턱을 못 넘어야
        한다."""
        section = _section(
            "C1",
            "R1",
            "본문",
            "당사는 무상증자를 결정하였고, 별도로 발행어음 관련 계약도 체결했습니다.",
        )
        bonus = _keyword_bonus_for(section, "증자 발행", ["증자", "발행"])
        assert bonus < DEFAULT_MIN_SCORE


class TestSearchOrdering:
    """Issue #32/DEC-005 — rcept_dt 최신순 tie-break(관련도가 주 기준, PR #34를 #35
    위로 리베이스하며 합의된 방식)."""

    def test_sorts_by_rcept_dt_descending(self) -> None:
        index = CompanyDisclosureIndex()
        index.index(
            [
                _section("C1", "R_OLD", "유상증자", "본문", rcept_dt="20260101"),
                _section("C1", "R_NEW", "유상증자", "본문", rcept_dt="20260901"),
                _section("C1", "R_MID", "유상증자", "본문", rcept_dt="20260601"),
            ]
        )

        result = index.search("C1", "유상증자")
        assert [s.rcept_no for s in result] == ["R_NEW", "R_MID", "R_OLD"]

    def test_missing_rcept_dt_sorts_last(self) -> None:
        index = CompanyDisclosureIndex()
        index.index(
            [
                _section("C1", "R_NO_DATE", "유상증자", "본문"),  # rcept_dt="" (기본값)
                _section("C1", "R_DATED", "유상증자", "본문", rcept_dt="20260101"),
            ]
        )

        result = index.search("C1", "")
        assert [s.rcept_no for s in result] == ["R_DATED", "R_NO_DATE"]

    def test_top_k_applies_after_sorting(self) -> None:
        index = CompanyDisclosureIndex()
        index.index(
            [
                _section("C1", "R_OLD", "유상증자", "본문", rcept_dt="20260101"),
                _section("C1", "R_NEW", "유상증자", "본문", rcept_dt="20260901"),
            ]
        )

        result = index.search("C1", "", top_k=1)
        assert [s.rcept_no for s in result] == ["R_NEW"]

    def test_ties_break_by_rcept_dt_when_scores_equal(self) -> None:
        """관련도 점수가 동률이면(동일 텍스트라 코사인 유사도·키워드 보너스가 같음)
        rcept_dt 최신순으로 tie-break한다."""
        index = CompanyDisclosureIndex()
        index.index(
            [
                _section("C1", "R_OLD", "유상증자", "본문", rcept_dt="20260101"),
                _section("C1", "R_NEW", "유상증자", "본문", rcept_dt="20260901"),
            ]
        )

        result = index.search("C1", "유상증자")
        assert [s.rcept_no for s in result] == ["R_NEW", "R_OLD"]


class TestLazyLoader:
    """2026-09-18 — 콜드스타트 지연 로딩. `_company_index()`(demo/app.py)가 부팅 시
    100개사를 전부 미리 임베딩하면 실측 40.6분이 걸려서, `search()`가 실제로 요청한
    회사만 그 자리에서 채우는 훅으로 바꿨다."""

    def test_lazy_loader_called_on_first_search_for_unseen_corp_code(self) -> None:
        calls: list[str] = []

        def loader(idx: CompanyDisclosureIndex, corp_code: str) -> None:
            calls.append(corp_code)
            idx.index([_section(corp_code, "R1", "유상증자", "본문")])

        index = CompanyDisclosureIndex(lazy_loader=loader)
        result = index.search("C1", "유상증자")

        assert calls == ["C1"]
        assert len(result) == 1

    def test_lazy_loader_not_called_again_once_indexed(self) -> None:
        """이미 채워진 corp_code는 로더를 다시 안 부른다 — 매 검색마다 재계산하면
        지연 로딩의 목적(한 번만 임베딩)이 무의미해진다."""
        calls: list[str] = []

        def loader(idx: CompanyDisclosureIndex, corp_code: str) -> None:
            calls.append(corp_code)
            idx.index([_section(corp_code, "R1", "유상증자", "본문")])

        index = CompanyDisclosureIndex(lazy_loader=loader)
        index.search("C1", "유상증자")
        index.search("C1", "유상증자")
        index.search("C1", "")

        assert calls == ["C1"]  # 딱 한 번만

    def test_lazy_loader_not_retried_when_company_has_no_data(self) -> None:
        """로더가 아무것도 못 채워도(코퍼스에 없는 회사) 매 검색마다 다시 부르지
        않는다 — "안 불러옴"과 "불러왔지만 데이터 없음"을 구분해야 한다."""
        calls: list[str] = []

        def loader(idx: CompanyDisclosureIndex, corp_code: str) -> None:
            calls.append(corp_code)
            # 아무것도 채우지 않음(코퍼스에 없는 회사를 흉내냄)

        index = CompanyDisclosureIndex(lazy_loader=loader)
        index.search("C_UNKNOWN", "유상증자")
        index.search("C_UNKNOWN", "유상증자")

        assert calls == ["C_UNKNOWN"]

    def test_no_lazy_loader_behaves_like_before(self) -> None:
        """`lazy_loader=None`(기본값)이면 예전과 동일하게 미리 채워진 것만 검색된다
        — 하위 호환."""
        index = CompanyDisclosureIndex()
        result = index.search("C1", "유상증자")

        assert result == []

    def test_explicitly_indexed_corp_code_skips_lazy_loader(self) -> None:
        """이미 `index()`로 직접 채운 corp_code는 로더를 부를 필요가 없다."""
        calls: list[str] = []

        def loader(idx: CompanyDisclosureIndex, corp_code: str) -> None:
            calls.append(corp_code)

        index = CompanyDisclosureIndex(lazy_loader=loader)
        index.index([_section("C1", "R1", "유상증자", "본문")])
        index.search("C1", "유상증자")

        assert calls == []


class TestConcurrentLazyLoad:
    """2026-09-22(상세 감사) — 외부 감사 에이전트가 `_indexed_keys`의 `add()`
    부근에 지연을 주입하는 방식(테스트 하네스 전용, 운영 코드는 안 건드림)으로
    check-then-add 경쟁 상태를 5/5 재현했다: 두 세션이 거의 동시에 같은(한 번도
    안 불러온) 종목을 조회하면 `_lazy_loader`가 중복 호출돼 섹션이 두 배로
    인덱싱될 수 있었다. 그 정확한 내부 타이밍(GIL 스케줄링에 의존)을 그대로
    재현하는 대신, `_lock_for(corp_code)` 도입이 실제로 보장해야 하는 성질 —
    "같은 corp_code에 대한 임계구역은 한 번에 한 스레드만 실행한다" — 을
    결정론적으로(Event 기반, 타이밍 운에 의존하지 않고) 검증한다."""

    def test_index_for_same_corp_code_serializes_across_threads(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import threading

        entered = threading.Event()
        release = threading.Event()

        def _blocking_embed_texts(texts: list[str]) -> np.ndarray:
            # 첫 스레드가 임계구역(락 보호 구간) 안에 들어왔음을 알리고, 신호를
            # 받을 때까지 그 안에서 대기한다 — 실제로는 무거운 모델 호출이 이
            # 역할을 한다(외부 감사가 지적한 그대로).
            entered.set()
            release.wait(timeout=5)
            return _fake_embed_texts(texts)

        monkeypatch.setattr(company_index_module, "embed_texts", _blocking_embed_texts)

        index = CompanyDisclosureIndex()
        section = _section("C1", "R1", "유상증자", "본문")

        t1 = threading.Thread(target=lambda: index.index([section]))
        t1.start()
        assert entered.wait(timeout=2)  # t1이 임계구역(embed_texts 호출 중) 안에 있음

        second_finished = threading.Event()

        def _second_index() -> None:
            index.index([section])  # 락이 있으면 t1이 끝날 때까지 여기서 대기해야 한다
            second_finished.set()

        t2 = threading.Thread(target=_second_index)
        t2.start()
        # t1이 아직 임계구역을 안 벗어났으므로, 락이 제대로 동작한다면 t2는
        # 이 시점에 아직 끝나 있으면 안 된다(락 없이 바로 통과했다면 즉시 끝났을 것).
        assert not second_finished.wait(timeout=0.3)

        release.set()  # t1이 마저 끝나게 한다 → t2도 락을 얻어 진행
        t1.join(timeout=5)
        t2.join(timeout=5)
        assert second_finished.is_set()

        sections, _ = index.dump_rows_for("C1")
        assert len(sections) == 1  # 같은 섹션이 중복 적재되지 않는다(existing_keys 재확인)


class TestIndexWithVectors:
    """`index_with_vectors()` — 디스크 캐시에서 이미 계산된 벡터를 재임베딩 없이
    적재한다(2026-09-18, 지연 로딩 캐시 적중 경로)."""

    def test_loads_precomputed_vectors_without_calling_embed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _fail_if_called(texts: list[str]) -> np.ndarray:
            raise AssertionError("embed_texts()가 호출되면 안 된다 — 벡터가 이미 있음")

        monkeypatch.setattr(company_index_module, "embed_texts", _fail_if_called)

        sections = [_section("C1", "R1", "유상증자", "MOU 체결")]
        vectors = _fake_embed_texts(["유상증자\nMOU 체결"])

        index = CompanyDisclosureIndex()
        index.index_with_vectors(sections, vectors)

        result = index.search("C1", "유상증자")
        assert len(result) == 1

    def test_duplicate_sections_skipped_same_as_index(self) -> None:
        sections = [_section("C1", "R1", "유상증자", "본문")]
        vectors = _fake_embed_texts(["유상증자\n본문"])

        index = CompanyDisclosureIndex()
        index.index_with_vectors(sections, vectors)
        index.index_with_vectors(sections, vectors)  # 같은 섹션 다시(Issue #80과 동일 원칙)

        result = index.search("C1", "")
        assert len(result) == 1

    def test_multiple_companies_in_one_call(self) -> None:
        sections = [
            _section("C1", "R1", "유상증자", "본문1"),
            _section("C2", "R2", "합병", "본문2"),
        ]
        vectors = _fake_embed_texts(["유상증자\n본문1", "합병\n본문2"])

        index = CompanyDisclosureIndex()
        index.index_with_vectors(sections, vectors)

        assert len(index.search("C1", "")) == 1
        assert len(index.search("C2", "")) == 1


class TestDumpRowsFor:
    """`dump_rows_for()` — 한 회사분만 직렬화(2026-09-18, 지연 로딩 캐시 저장용)."""

    def test_returns_only_requested_company(self) -> None:
        index = CompanyDisclosureIndex()
        index.index(
            [
                _section("C1", "R1", "유상증자", "본문1"),
                _section("C2", "R2", "합병", "본문2"),
            ]
        )

        sections, vectors = index.dump_rows_for("C1")

        assert [s.rcept_no for s in sections] == ["R1"]
        assert len(vectors) == 1

    def test_unknown_company_returns_empty(self) -> None:
        index = CompanyDisclosureIndex()

        sections, vectors = index.dump_rows_for("C_UNKNOWN")

        assert sections == []
        assert len(vectors) == 0

    def test_round_trips_through_index_with_vectors(self) -> None:
        """dump_rows_for()로 뽑은 걸 index_with_vectors()에 그대로 넣으면 같은
        검색 결과가 나와야 한다(캐시 저장->로드 왕복 검증)."""
        original = CompanyDisclosureIndex()
        original.index(
            [
                _section("C1", "R1", "유상증자", "본문1"),
                _section("C1", "R2", "합병", "본문2"),
            ]
        )
        sections, vectors = original.dump_rows_for("C1")

        restored = CompanyDisclosureIndex()
        restored.index_with_vectors(sections, vectors)

        assert {s.rcept_no for s in restored.search("C1", "")} == {"R1", "R2"}
