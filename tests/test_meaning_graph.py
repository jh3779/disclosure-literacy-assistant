"""demo/app.py::_build_meaning_graph_dot 단위 테스트.

2026-09-18 재구성: "사후이벤트"를 미래 사건이 아니라 그 표현이 원래 속한 공시
유형(form_type) 자체로 재정의했다 — 체인은 회사→표현→의미(form_type)가 기본이고,
검증된 자기상관 통계(recurrence)는 **과거 시제로만** 참고 노드로 붙는다("반복돼
왔다"O, "반복될 것"X). `trend_hit`(회사 자신의 과거 대비 최근 흐름)도 이미 지나간
두 구간을 비교할 뿐이라 미래를 주장하지 않는다.

실측 버그 재현(2026-09-17) — `recurrence`/`trend` 노드 라벨을 조립한 뒤 다시
`_escape_dot_label()`을 통과시키면, 그 함수의 백슬래시 치환(`\\` -> `/`)이 라벨에
이미 넣어둔 DOT 줄바꿈 이스케이프(`\\n`)까지 `/n`으로 깨뜨렸다. 동적 텍스트 조각에만
이스케이프를 적용하고, 이미 조립된(줄바꿈 포함) 문자열은 다시 통과시키지 않아야 한다.
"""

from __future__ import annotations

from disclosure_lit.demo.app import _build_meaning_graph_dot
from disclosure_lit.dictionary.company_trend import CompanyTrendItem
from disclosure_lit.dictionary.recurrence import RecurrenceSignal


class TestBuildMeaningGraphDot:
    def test_minimal_graph_has_no_dangling_backslash_n(self) -> None:
        dot = _build_meaning_graph_dot(
            corp_name="(종목 미특정)",
            representative_phrase="테스트 문구입니다.",
            form_type="기타",
            purity=0.6,
            trend_hit=None,
            recurrence=None,
        )

        assert "/n" not in dot  # 실측 버그 재현 — 고쳤으면 이 문자열이 나오면 안 됨
        assert "\\n" in dot  # 줄바꿈 이스케이프는 살아있어야 함
        assert "company" in dot and "phrase" in dot and "meaning" in dot
        assert "recurrence" not in dot  # None이면 그 노드는 안 만들어짐
        assert "trend" not in dot

    def test_recurrence_node_uses_past_tense_only(self) -> None:
        """재정의 원칙 — 자기상관 노드는 검증됐어도 "다시 나타날 것"이라는 미래형
        문구를 쓰면 안 된다. "반복돼 왔다"류 과거형만 허용한다."""
        dot = _build_meaning_graph_dot(
            corp_name="삼성전자",
            representative_phrase="재무구조를 개선하였습니다.",
            form_type="전환사채_발행",
            purity=0.87,
            trend_hit=None,
            recurrence=RecurrenceSignal(
                form_type="전환사채_발행", stability=0.72, n_train_significant=21, n_seeds=30
            ),
        )

        assert "/n" not in dot
        assert "그중 72%가 다른 절반에서도 재현" in dot
        assert "meaning -> recurrence;" in dot
        assert "될 것" not in dot  # 미래형 표현 금지
        assert "할 것" not in dot

    def test_trend_node_keeps_newline_escape_intact(self) -> None:
        dot = _build_meaning_graph_dot(
            corp_name="삼성전자",
            representative_phrase="재무구조를 개선하였습니다.",
            form_type="전환사채_발행",
            purity=0.87,
            trend_hit=CompanyTrendItem(
                form_type="전환사채_발행",
                recent_count=3,
                recent_rate=0.5,
                baseline_count=1,
                baseline_rate=0.1,
                is_increasing=True,
            ),
            recurrence=None,
        )

        assert "/n" not in dot
        assert "비중 증가" in dot
        assert "meaning -> trend;" in dot

    def test_decreasing_trend_shows_decrease_label(self) -> None:
        dot = _build_meaning_graph_dot(
            corp_name="삼성전자",
            representative_phrase="문구",
            form_type="유상증자",
            purity=0.9,
            trend_hit=CompanyTrendItem(
                form_type="유상증자",
                recent_count=0,
                recent_rate=0.0,
                baseline_count=5,
                baseline_rate=0.5,
                is_increasing=False,
            ),
            recurrence=None,
        )

        assert "감소" in dot

    def test_both_recurrence_and_trend_can_appear_together(self) -> None:
        dot = _build_meaning_graph_dot(
            corp_name="삼성전자",
            representative_phrase="문구",
            form_type="전환사채_발행",
            purity=0.9,
            trend_hit=CompanyTrendItem(
                form_type="전환사채_발행",
                recent_count=3,
                recent_rate=0.5,
                baseline_count=1,
                baseline_rate=0.1,
                is_increasing=True,
            ),
            recurrence=RecurrenceSignal(
                form_type="전환사채_발행", stability=1.0, n_train_significant=30, n_seeds=30
            ),
        )

        assert "meaning -> recurrence;" in dot
        assert "meaning -> trend;" in dot

    def test_dynamic_text_with_quotes_does_not_break_dot_syntax(self) -> None:
        """대표 문구/회사명에 인용부호가 섞여도 DOT 문법이 깨지면 안 된다."""
        dot = _build_meaning_graph_dot(
            corp_name='회사"이름',
            representative_phrase='문구에 "인용"이 있습니다.',
            form_type="기타",
            purity=0.5,
            trend_hit=None,
            recurrence=None,
        )

        # 원본 인용부호가 라벨 문자열의 구분자로 잘못 쓰이지 않아야 한다.
        assert '회사"이름' not in dot
        assert "digraph G {" in dot


class TestRegulatoryWording:
    """2026-09-22 규제 방어선 점검 — 그래프 라벨이 투자 신호나 회사 평가 지표처럼 읽히면
    안 된다(03-integrated-proposal.md 7절: "신빙성 신호등·종합 등급 등 종합 판단 지표
    배제"). 문구가 나중에 다시 바뀌어도 이 방어선이 조용히 무너지지 않게 고정한다."""

    def _dot(self, *, increasing: bool) -> str:
        return _build_meaning_graph_dot(
            corp_name="삼성전자",
            representative_phrase="문구",
            form_type="전환사채_발행",
            purity=0.9,
            trend_hit=CompanyTrendItem(
                form_type="전환사채_발행",
                recent_count=3,
                recent_rate=0.5,
                baseline_count=1,
                baseline_rate=0.1,
                is_increasing=increasing,
            ),
            recurrence=RecurrenceSignal(
                form_type="전환사채_발행", stability=0.72, n_train_significant=21, n_seeds=30
            ),
        )

    def test_no_directional_market_icons(self) -> None:
        """📈/📉는 금융 서비스에서 곧 주가 상승·하락 아이콘이다 — 예전엔 "감소"일 때도
        📈가 떴다."""
        for increasing in (True, False):
            dot = self._dot(increasing=increasing)
            assert "📈" not in dot
            assert "📉" not in dot

    def test_trend_label_names_what_is_changing(self) -> None:
        """ "최근 흐름: 증가"만 쓰면 주가 추세로 읽힌다 — 무엇의(이 회사 공시 중 해당
        유형 비중) 변화인지 라벨에 있어야 한다."""
        dot = self._dot(increasing=True)
        assert "이 회사 공시 중 '전환사채_발행' 비중" in dot
        assert "최근 흐름" not in dot

    def test_trend_hangs_off_the_form_type_not_the_company(self) -> None:
        """회사 노드에 붙으면 "회사의 흐름"(=실적·주가)으로 읽힌다."""
        dot = self._dot(increasing=True)
        assert "company -> trend;" not in dot
        assert "meaning -> trend;" in dot

    def test_no_word_that_reads_as_a_company_rating(self) -> None:
        """ "안정성"은 금융 맥락에서 재무 건전성 지표로 읽힌다."""
        dot = self._dot(increasing=True)
        for word in ("안정성", "건전성", "등급", "신호", "추천"):
            assert word not in dot, word
