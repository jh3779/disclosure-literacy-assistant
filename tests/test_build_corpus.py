"""scripts/build_corpus.py(A10) — 종목 단위 실패 격리.

PR #41 리뷰 지적(@jh3779): 한 종목의 list.json 이 실패하면 예외가 루프 밖으로 올라가
100개사 배치 전체가 멈췄다. 재실행해도 매번 같은 종목에서 다시 죽어 그 뒤 종목들이
영구히 수집되지 않는 구조라, 운영성 실패(DART 오류·네트워크 오류)는 종목 단위로
격리하고 코드 버그는 그대로 올라가게 한 것을 여기서 못박는다.

scripts/ 는 패키지가 아니라서(`python scripts/build_corpus.py` 로 실행) 테스트에서는
sys.path 에 scripts/ 를 넣고 모듈로 임포트한다 — build_corpus.py 자신도 같은 방식으로
collect_disclosures 를 임포트한다.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import build_corpus  # noqa: E402
from collect_disclosures import CollectionStats, FailureRecord  # noqa: E402

from disclosure_lit.dart.client import DartApiError, DartRateLimitError  # noqa: E402


def _stats(corp_code: str, *, failures: list[FailureRecord] | None = None) -> CollectionStats:
    failures = failures or []
    return CollectionStats(
        corp_code=corp_code,
        ok=1,
        skipped=0,
        failed=len(failures),
        total_disclosures=1,
        out_dir=Path("unused"),
        sections_path=Path("unused"),
        failures=failures,
    )


def _run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fail_with: dict[str, BaseException]
) -> list[str]:
    """3개사로 main()을 돌리고, collect_for_company 가 실제로 호출된 corp_code 목록을 반환한다."""
    companies = [{"corp_code": f"C{i}", "corp_name": f"회사{i}"} for i in (1, 2, 3)]
    companies_file = tmp_path / "companies.json"
    companies_file.write_text(json.dumps(companies, ensure_ascii=False), encoding="utf-8")

    attempted: list[str] = []

    def fake_collect(client: object, corp_code: str, **kwargs: object) -> CollectionStats:
        attempted.append(corp_code)
        if corp_code in fail_with:
            raise fail_with[corp_code]
        return _stats(corp_code)

    monkeypatch.setattr(build_corpus, "collect_for_company", fake_collect)
    monkeypatch.setattr(build_corpus, "DartClient", lambda api_key: object())
    monkeypatch.setattr(build_corpus, "fix_console_encoding", lambda: None)
    monkeypatch.setattr(sys, "argv", ["build_corpus.py", "--companies-file", str(companies_file)])

    build_corpus.main()
    return attempted


class TestCompanyFailureIsolation:
    def test_dart_error_on_one_company_does_not_stop_batch(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        attempted = _run(monkeypatch, tmp_path, {"C2": DartApiError("800", "시스템 점검중")})

        # 예전엔 C2 에서 멈춰 C3 는 시도조차 안 됐다.
        assert attempted == ["C1", "C2", "C3"]
        err = capsys.readouterr().err
        assert "종목 단위 실패 1개사" in err
        assert "회사2 (C2)" in err

    def test_rate_limit_exhausted_is_isolated_too(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """DartRateLimitError 는 DartApiError 하위 — @retry 소진 후 올라와도 격리된다."""
        attempted = _run(monkeypatch, tmp_path, {"C1": DartRateLimitError("020", "요청 제한")})

        assert attempted == ["C1", "C2", "C3"]

    def test_network_error_is_isolated(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        attempted = _run(monkeypatch, tmp_path, {"C2": httpx.ConnectTimeout("timeout")})

        assert attempted == ["C1", "C2", "C3"]

    def test_code_bug_still_propagates(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """운영성 실패만 격리한다 — 코드 버그까지 삼키면 100개사에 걸쳐 조용히 건너뛴다."""
        with pytest.raises(KeyError):
            _run(monkeypatch, tmp_path, {"C2": KeyError("rcept_no")})

    def test_no_failure_summary_when_all_succeed(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        attempted = _run(monkeypatch, tmp_path, {})

        assert attempted == ["C1", "C2", "C3"]
        assert "종목 단위 실패" not in capsys.readouterr().err


class TestFailureRecordAggregation:
    """Issue #45 — 공시 단위 실패 상세가 회사별로 흩어지지 않고 배치 전체로 모여
    companies_path 옆의 failures.jsonl 하나에 남는지 확인한다."""

    def _run_with_disclosure_failures(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        failures_by_company: dict[str, list[FailureRecord]],
    ) -> Path:
        companies = [{"corp_code": f"C{i}", "corp_name": f"회사{i}"} for i in (1, 2, 3)]
        companies_file = tmp_path / "companies.json"
        companies_file.write_text(json.dumps(companies, ensure_ascii=False), encoding="utf-8")

        def fake_collect(client: object, corp_code: str, **kwargs: object) -> CollectionStats:
            return _stats(corp_code, failures=failures_by_company.get(corp_code, []))

        monkeypatch.setattr(build_corpus, "collect_for_company", fake_collect)
        monkeypatch.setattr(build_corpus, "DartClient", lambda api_key: object())
        monkeypatch.setattr(build_corpus, "fix_console_encoding", lambda: None)
        monkeypatch.setattr(
            sys, "argv", ["build_corpus.py", "--companies-file", str(companies_file)]
        )

        build_corpus.main()
        return tmp_path / "failures.jsonl"

    def test_failures_across_companies_written_to_one_file(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        failures_path = self._run_with_disclosure_failures(
            monkeypatch,
            tmp_path,
            {
                "C1": [FailureRecord(corp_code="C1", rcept_no="R1", report_nm="A", error="e1")],
                "C3": [FailureRecord(corp_code="C3", rcept_no="R3", report_nm="C", error="e3")],
            },
        )

        assert failures_path.exists()
        lines = failures_path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["rcept_no"] == "R1"
        assert json.loads(lines[1])["rcept_no"] == "R3"

    def test_summary_line_printed_when_disclosure_failures_exist(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._run_with_disclosure_failures(
            monkeypatch,
            tmp_path,
            {"C2": [FailureRecord(corp_code="C2", rcept_no="R2", report_nm="B", error="e2")]},
        )

        assert "공시 단위 실패 상세(1건)" in capsys.readouterr().err

    def test_no_file_or_summary_when_no_disclosure_failures(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        failures_path = self._run_with_disclosure_failures(monkeypatch, tmp_path, {})

        assert not failures_path.exists()
        assert "공시 단위 실패 상세" not in capsys.readouterr().err
