"""L5 — RumorGoldCase 스키마 + load_gold_cases 단위 테스트."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from disclosure_lit.eval.gold_schema import DEFAULT_GOLD_PATH, RumorGoldCase, load_gold_cases


class TestRumorGoldCase:
    def test_valid_case_parses(self) -> None:
        case = RumorGoldCase(
            rumor_text="테스트 루머",
            corp_code="00000000",
            expected_facts=["사실 하나"],
            source_url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=1",
        )
        assert case.corp_code == "00000000"

    def test_missing_field_raises(self) -> None:
        with pytest.raises(ValidationError):
            RumorGoldCase(rumor_text="루머", corp_code="1")  # expected_facts/source_url 누락


class TestLoadGoldCases:
    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        assert load_gold_cases(tmp_path / "없음.jsonl") == []

    def test_roundtrips_gold_case(self, tmp_path: Path) -> None:
        case = RumorGoldCase(
            rumor_text="루머",
            corp_code="00164779",
            expected_facts=["사실1", "사실2"],
            source_url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=1",
        )
        path = tmp_path / "gold.jsonl"
        path.write_text(json.dumps(case.model_dump(), ensure_ascii=False) + "\n", encoding="utf-8")

        loaded = load_gold_cases(path)

        assert loaded == [case]

    def test_skips_blank_lines(self, tmp_path: Path) -> None:
        path = tmp_path / "gold.jsonl"
        path.write_text("\n\n", encoding="utf-8")
        assert load_gold_cases(path) == []

    def test_default_seed_file_has_real_verified_cases(self) -> None:
        """rumor_cases.jsonl — 전부 실제 DART "풍문 또는 보도에 대한 해명" 공시에서
        가져온 것(2026-09-14, 29개 대형주 1년치 수집 후 정규식 추출 + 중복 제거).
        REQ-F-008(gold set 20~30건) 목표 달성을 회귀 감시한다."""
        cases = load_gold_cases(DEFAULT_GOLD_PATH)

        assert len(cases) >= 20
        assert all(c.source_url.startswith("https://dart.fss.or.kr/") for c in cases)
        assert all(c.expected_facts for c in cases)
        # 종목 하나에 쏠리지 않고 여러 회사에 걸쳐 있어야 팩트체크 일반화 검증에 의미가 있다.
        assert len({c.corp_code for c in cases}) >= 10
