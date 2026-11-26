"""A12 — label_post_events.py 단위 테스트. 담당: A(이승노).

scripts/ 는 패키지가 아니라서(`python scripts/label_post_events.py` 로 실행)
tests/test_build_corpus.py 와 같은 방식으로 sys.path 에 scripts/ 를 넣고 모듈로 임포트한다.
DART API 호출이 없는 순수 날짜 계산 로직이라 합성 공시 딕셔너리만으로 검증한다.
"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from label_post_events import label_post_events  # noqa: E402


def _d(corp_code: str, rcept_no: str, rcept_dt: str, form_type: str) -> dict:
    return {
        "corp_code": corp_code,
        "rcept_no": rcept_no,
        "rcept_dt": rcept_dt,
        "form_type": form_type,
    }


class TestLabelPostEvents:
    def test_finds_outcome_event_within_window(self) -> None:
        disclosures = [
            _d("C1", "R1", "20260101", "MOU"),
            _d("C1", "R2", "20260201", "유상증자"),
        ]

        labels = label_post_events(disclosures, lookback_months=3)

        r1 = next(lbl for lbl in labels if lbl.rcept_no == "R1")
        assert r1.outcome_events == ["유상증자"]
        assert r1.outcome_count == 1

    def test_outcome_outside_window_not_counted(self) -> None:
        disclosures = [
            _d("C1", "R1", "20260101", "MOU"),
            _d("C1", "R2", "20260601", "유상증자"),  # 5개월 뒤 — 3개월 창 밖
        ]

        labels = label_post_events(disclosures, lookback_months=3)

        r1 = next(lbl for lbl in labels if lbl.rcept_no == "R1")
        assert r1.outcome_events == []
        assert r1.outcome_count == 0

    def test_non_outcome_form_type_not_counted(self) -> None:
        """정기공시·지분보고처럼 일상적인 공시는 사후 이벤트로 안 센다."""
        disclosures = [
            _d("C1", "R1", "20260101", "MOU"),
            _d("C1", "R2", "20260115", "정기공시"),
        ]

        labels = label_post_events(disclosures, lookback_months=3)

        r1 = next(lbl for lbl in labels if lbl.rcept_no == "R1")
        assert r1.outcome_events == []

    def test_does_not_count_itself_as_outcome(self) -> None:
        """공시 자신의 form_type이 OUTCOME_FORM_TYPES에 속해도 자기 자신은 사후 이벤트가
        아니다 — 관찰 기간은 그 공시 "이후"만 본다."""
        disclosures = [_d("C1", "R1", "20260101", "유상증자")]

        labels = label_post_events(disclosures, lookback_months=3)

        assert labels[0].outcome_events == []
        assert labels[0].outcome_count == 0

    def test_multiple_outcomes_in_window_all_collected(self) -> None:
        disclosures = [
            _d("C1", "R1", "20260101", "MOU"),
            _d("C1", "R2", "20260110", "유상증자"),
            _d("C1", "R3", "20260120", "회사합병"),
        ]

        labels = label_post_events(disclosures, lookback_months=3)

        r1 = next(lbl for lbl in labels if lbl.rcept_no == "R1")
        assert r1.outcome_events == ["유상증자", "회사합병"]
        assert r1.outcome_count == 2

    def test_same_outcome_type_repeated_counts_toward_outcome_count_but_once_in_events(
        self,
    ) -> None:
        disclosures = [
            _d("C1", "R1", "20260101", "MOU"),
            _d("C1", "R2", "20260105", "유상증자"),
            _d("C1", "R3", "20260110", "유상증자"),
        ]

        labels = label_post_events(disclosures, lookback_months=3)

        r1 = next(lbl for lbl in labels if lbl.rcept_no == "R1")
        assert r1.outcome_events == ["유상증자"]  # 종류는 1개
        assert r1.outcome_count == 2  # 건수는 2건

    def test_different_companies_do_not_leak_into_each_other(self) -> None:
        disclosures = [
            _d("C1", "R1", "20260101", "MOU"),
            _d("C2", "R2", "20260105", "유상증자"),  # 다른 회사
        ]

        labels = label_post_events(disclosures, lookback_months=3)

        r1 = next(lbl for lbl in labels if lbl.rcept_no == "R1")
        assert r1.outcome_events == []

    def test_window_near_corpus_boundary_is_censored(self) -> None:
        """코퍼스에서 관측된 가장 최근 날짜 근처의 공시는 관찰 기간이 실제 수집 경계를
        넘어가므로, "이벤트 없음"이 아니라 "아직 다 못 봄"(우측 절단)으로 표시해야 한다."""
        disclosures = [
            _d("C1", "R1", "20260101", "MOU"),
            _d("C1", "R2", "20260315", "기타"),  # 코퍼스 최신 날짜
        ]

        labels = label_post_events(disclosures, lookback_months=3)

        r2 = next(lbl for lbl in labels if lbl.rcept_no == "R2")
        assert r2.is_censored is True  # 20260315 + 3개월 > 코퍼스 최대 날짜(20260315)

    def test_window_safely_within_corpus_range_is_not_censored(self) -> None:
        disclosures = [
            _d("C1", "R1", "20260101", "MOU"),
            _d("C1", "R2", "20260901", "기타"),  # 코퍼스 최대 날짜보다 한참 이전
        ]

        labels = label_post_events(disclosures, lookback_months=3)

        r1 = next(lbl for lbl in labels if lbl.rcept_no == "R1")
        assert r1.is_censored is False  # 20260101 + 3개월(20260401) <= 코퍼스 최대(20260901)

    def test_sparse_company_with_single_disclosure(self) -> None:
        """공시가 하나뿐인 회사도 크래시 없이 처리돼야 한다(실측 — 최소 표본 회사 존재)."""
        disclosures = [_d("C1", "R1", "20260101", "MOU")]

        labels = label_post_events(disclosures, lookback_months=3)

        assert len(labels) == 1
        assert labels[0].outcome_count == 0
