"""scripts/select_corpus_companies.py(A10) — 후보 스캔 네트워크 오류 격리(Issue #44) +
corp_code.zip 캐시 TTL(Issue #47).

PR #41 리뷰 후 발견(@jh3779), 둘 다 이 스크립트가 대상이라 이 파일에서 같이 다룬다:

- **Issue #44**: 후보 스캔 루프가 `DartApiError`만 잡고 있어서 `httpx.ConnectError`/
  `ReadTimeout` 같은 일시적 네트워크 오류는 그대로 위로 던져져 `main()` 전체가 죽었다
  — 결과 저장(`out_path.write_text`)이 루프 끝에서만 실행되니 이미 조회 완료한
  후보들이 전부 버려졌다. 이 파일 자체가 이 스크립트의 첫 테스트였다(이슈가 지적한
  대로 이전까지 테스트가 0건이었다).
- **Issue #47**: `_fetch_zip_or_raise`(disclosure_lit/dart/client.py) 캐시 로직이
  파일이 있으면 나이·수정일 확인 없이 무조건 그대로 반환한다 — 몇 주~몇 달 뒤
  코퍼스를 보강/재추출할 때도 그대로 재사용돼, 그사이 신규 상장·상장폐지가 반영
  안 되는데 경고가 없었다.

scripts/ 는 패키지가 아니라서 test_build_corpus.py 와 같은 방식으로 sys.path 에
scripts/ 를 넣고 모듈로 임포트한다.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import httpx
import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import select_corpus_companies  # noqa: E402

from disclosure_lit.dart.client import (  # noqa: E402
    CORP_CLASS_KOSDAQ,
    CompanyInfo,
    CorpCodeEntry,
    DartApiError,
)


def _entry(corp_code: str) -> CorpCodeEntry:
    return CorpCodeEntry(
        corp_code=corp_code,
        corp_name=f"회사{corp_code}",
        stock_code=f"{corp_code}0",
        modify_date="20260101",
    )


def _info(corp_code: str) -> CompanyInfo:
    return CompanyInfo(
        corp_code=corp_code,
        corp_name=f"회사{corp_code}",
        stock_code=f"{corp_code}0",
        corp_cls=CORP_CLASS_KOSDAQ,
        induty_code="62",
    )


class _FakeClient:
    """Issue #44 테스트용 — 후보별로 `fetch_company_info` 실패를 흉내낸다."""

    def __init__(self, entries: list[CorpCodeEntry], fail_with: dict[str, BaseException]) -> None:
        self._entries = entries
        self._fail_with = fail_with

    def fetch_corp_codes(self, *, cache_path: Path | None = None) -> list[CorpCodeEntry]:
        return self._entries

    def fetch_company_info(self, corp_code: str) -> CompanyInfo:
        if corp_code in self._fail_with:
            raise self._fail_with[corp_code]
        return _info(corp_code)

    def list_disclosures(self, corp_code: str, *, bgn_de: str, end_de: str) -> list:
        return []


class _CacheAwareFakeClient:
    """Issue #47 테스트용 — `fetch_corp_codes`의 캐시 파일 읽기/쓰기 동작을 흉내낸다."""

    def __init__(self, entries: list[CorpCodeEntry]) -> None:
        self._entries = entries
        self.fetch_corp_codes_calls = 0

    def fetch_corp_codes(self, *, cache_path: Path | None = None) -> list[CorpCodeEntry]:
        self.fetch_corp_codes_calls += 1
        # 실제 클라이언트의 캐시 로직(disclosure_lit/dart/client.py::_fetch_zip_or_raise)을
        # 흉내낸다 — 파일이 있으면 그대로 반환, 없으면 "새로 받아서" 캐시 파일을 만든다.
        if cache_path is not None and cache_path.exists():
            return self._entries
        if cache_path is not None:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_bytes(b"fresh")
        return self._entries

    def fetch_company_info(self, corp_code: str) -> CompanyInfo:
        return _info(corp_code)

    def list_disclosures(self, corp_code: str, *, bgn_de: str, end_de: str) -> list:
        return []


def _run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fail_with: dict[str, BaseException],
    *,
    n_candidates: int = 3,
) -> Path:
    entries = [_entry(f"C{i}") for i in range(1, n_candidates + 1)]
    out_path = tmp_path / "corpus_companies.json"

    monkeypatch.setattr(
        select_corpus_companies, "DartClient", lambda api_key: _FakeClient(entries, fail_with)
    )
    monkeypatch.setattr(select_corpus_companies, "fix_console_encoding", lambda: None)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "select_corpus_companies.py",
            "--target",
            str(n_candidates),
            "--candidate-pool",
            str(n_candidates),
            "--out",
            str(out_path),
            # 이 파일의 테스트들은 네트워크 오류 격리/캐시 TTL이 관심사라, 2026-09-17에
            # 추가된 희귀 이벤트 스코어링(진짜 프로젝트의 data/raw/post_event_labels.jsonl
            # 을 기본으로 읽음)이 끼어들면 무관한 실제 데이터에 테스트가 의존하게 된다 —
            # 명시적으로 꺼서 이 파일이 여전히 완전히 격리되게 한다.
            "--skip-rare-event-scoring",
        ],
    )

    select_corpus_companies.main()
    return out_path


def _run_main(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, extra_args: list[str] | None = None
) -> Path:
    entries = [_entry("C1")]
    out_path = tmp_path / "corpus_companies.json"
    cache_path = tmp_path / "corp_code.zip"

    monkeypatch.setattr(select_corpus_companies, "CORP_CODE_CACHE", cache_path)
    monkeypatch.setattr(
        select_corpus_companies, "DartClient", lambda api_key: _CacheAwareFakeClient(entries)
    )
    monkeypatch.setattr(select_corpus_companies, "fix_console_encoding", lambda: None)
    argv = [
        "select_corpus_companies.py",
        "--target",
        "1",
        "--candidate-pool",
        "1",
        "--out",
        str(out_path),
        "--skip-rare-event-scoring",  # 이 헬퍼는 캐시 TTL 동작만 검증한다 — 위 _run() 참고
        *(extra_args or []),
    ]
    monkeypatch.setattr(sys, "argv", argv)

    select_corpus_companies.main()
    return out_path


class TestNetworkErrorIsolation:
    def test_connect_error_on_one_candidate_does_not_crash_batch(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """예전엔 여기서 main() 전체가 죽어 out_path 가 아예 안 만들어졌다."""
        out_path = _run(monkeypatch, tmp_path, {"C2": httpx.ConnectError("connection refused")})

        assert out_path.exists()
        selected = json.loads(out_path.read_text(encoding="utf-8"))
        # C2 는 실패해서 제외되고, C1/C3 는 정상적으로 선정된다.
        assert {c["corp_code"] for c in selected} == {"C1", "C3"}

    def test_read_timeout_on_one_candidate_does_not_crash_batch(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        out_path = _run(monkeypatch, tmp_path, {"C1": httpx.ReadTimeout("timed out")})

        assert out_path.exists()

    def test_dart_api_error_still_isolated_as_before(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """httpx.HTTPError 를 추가로 잡게 됐어도 기존 DartApiError 처리(회귀)는 그대로."""
        out_path = _run(monkeypatch, tmp_path, {"C2": DartApiError("800", "점검중")})

        selected = json.loads(out_path.read_text(encoding="utf-8"))
        assert {c["corp_code"] for c in selected} == {"C1", "C3"}

    def test_failure_reason_printed_with_exception_type(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _run(monkeypatch, tmp_path, {"C2": httpx.ConnectError("connection refused")})

        err = capsys.readouterr().err
        assert "ConnectError" in err
        assert "회사C2" in err

    def test_code_bug_still_propagates(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """운영성 실패(네트워크·API 오류)만 격리한다 — 코드 버그까지 삼키면 400개
        후보에 걸쳐 조용히 건너뛴다(build_corpus.py 와 같은 원칙)."""
        with pytest.raises(KeyError):
            _run(monkeypatch, tmp_path, {"C2": KeyError("corp_code")})

    def test_all_succeed_selects_every_candidate(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        out_path = _run(monkeypatch, tmp_path, {})

        selected = json.loads(out_path.read_text(encoding="utf-8"))
        assert {c["corp_code"] for c in selected} == {"C1", "C2", "C3"}


class TestWarnIfCorpCodeCacheStale:
    def test_no_warning_when_cache_absent(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        select_corpus_companies._warn_if_corp_code_cache_stale(tmp_path / "없음.zip")

        assert capsys.readouterr().err == ""

    def test_no_warning_when_cache_fresh(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        cache_path = tmp_path / "corp_code.zip"
        cache_path.write_bytes(b"x")

        select_corpus_companies._warn_if_corp_code_cache_stale(cache_path)

        assert capsys.readouterr().err == ""

    def test_warns_when_cache_older_than_threshold(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        cache_path = tmp_path / "corp_code.zip"
        cache_path.write_bytes(b"x")
        stale_seconds = (select_corpus_companies.CORP_CODE_CACHE_STALE_DAYS + 1) * 86400
        old_time = time.time() - stale_seconds
        os.utime(cache_path, (old_time, old_time))

        select_corpus_companies._warn_if_corp_code_cache_stale(cache_path)

        err = capsys.readouterr().err
        assert "corp_code.zip 캐시" in err
        assert "--refresh-cache" in err


class TestLoadExcludedCorpCodes:
    """2026-09-17 추가 — 코퍼스 "확장"이 아니라 "변동": 재실행 시 이미 선정된 회사를
    다시 후보로 안 뽑으려면 이전 결과에서 corp_code만 뽑아내야 한다."""

    def test_missing_file_returns_empty_set(self, tmp_path: Path) -> None:
        assert select_corpus_companies._load_excluded_corp_codes(tmp_path / "없음.json") == set()

    def test_returns_corp_codes_from_existing_file(self, tmp_path: Path) -> None:
        path = tmp_path / "corpus_companies.json"
        path.write_text(
            json.dumps([{"corp_code": "C1"}, {"corp_code": "C2"}], ensure_ascii=False),
            encoding="utf-8",
        )

        assert select_corpus_companies._load_excluded_corp_codes(path) == {"C1", "C2"}


class TestRareOutcomeTypes:
    """`OUTCOME_FORM_TYPES`(label_post_events.py) 중 지금 코퍼스에 표본이 적은 하위
    절반을 찾아, 신규 회사 선정 시 그 유형을 겪은 후보를 우선하기 위한 기준."""

    def test_missing_file_returns_empty_set(self, tmp_path: Path) -> None:
        assert select_corpus_companies._rare_outcome_types(tmp_path / "없음.jsonl") == set()

    def test_only_observed_type_is_not_rare(self, tmp_path: Path) -> None:
        """딱 한 유형만 실제로 관측시키면(나머지 19종은 0건), 관측 카운트가 다른
        모든 0건짜리보다 확실히 크므로 정렬상 항상 맨 끝(하위 절반 밖)에 놓인다 —
        어느 0건짜리가 동점 처리로 하위 절반에 들어가는지는 임의적이라 특정 유형을
        지목하지 않는다."""
        path = tmp_path / "post_event_labels.jsonl"
        rows = [
            {"corp_code": f"C{i}", "outcome_events": ["유상증자"], "is_censored": False}
            for i in range(5)
        ]
        path.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8"
        )

        rare = select_corpus_companies._rare_outcome_types(path)

        assert "유상증자" not in rare
        assert len(rare) == 10  # bottom_fraction=0.5 기본값 x 20종

    def test_censored_labels_excluded_from_counting(self, tmp_path: Path) -> None:
        """label_post_events.py의 우측 절단 원칙과 동일 — 절단된 라벨은 "관측 없음"과
        구분해서 세면 안 되므로 카운트에서 빼야 한다. 절단된 라벨 1건만 있는 결과가
        "아무 라벨도 없는" 결과와 완전히 같아야(전부 0건 취급) 증명된다."""
        censored_path = tmp_path / "censored.jsonl"
        censored_path.write_text(
            json.dumps(
                {"corp_code": "C1", "outcome_events": ["유상증자"], "is_censored": True},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        empty_path = tmp_path / "empty.jsonl"
        empty_path.write_text("", encoding="utf-8")

        assert select_corpus_companies._rare_outcome_types(
            censored_path
        ) == select_corpus_companies._rare_outcome_types(empty_path)

    def test_same_company_repeating_does_not_change_result(self, tmp_path: Path) -> None:
        """dictionary/stats.py의 회사 단위 중복 제거와 같은 원칙 — 한 회사가 30번
        반복 제출해도 1번 제출한 것과 결과가 같아야 한다(반복이 희귀도 판단을
        왜곡하면 안 된다)."""
        repeated_path = tmp_path / "repeated.jsonl"
        rows = [{"corp_code": "C1", "outcome_events": ["유상증자"], "is_censored": False}] * 30
        repeated_path.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8"
        )

        once_path = tmp_path / "once.jsonl"
        once_path.write_text(json.dumps(rows[0], ensure_ascii=False), encoding="utf-8")

        assert select_corpus_companies._rare_outcome_types(
            repeated_path
        ) == select_corpus_companies._rare_outcome_types(once_path)


class _StubMeta:
    def __init__(self, report_nm: str) -> None:
        self.report_nm = report_nm


class TestRareEventScore:
    """실제 DART API를 부르는 대신 `list_disclosures`를 흉내낸 스텁으로 검증한다."""

    class _StubClient:
        def __init__(self, metas: list[_StubMeta]) -> None:
            self._metas = metas

        def list_disclosures(self, corp_code: str, *, bgn_de: str, end_de: str) -> list[_StubMeta]:
            return self._metas

    def test_counts_distinct_rare_type_hits_not_raw_occurrences(self) -> None:
        client = self._StubClient(
            [
                _StubMeta("유상증자결정"),
                _StubMeta("유상증자결정"),  # 같은 유형 반복 — 1종류로만 잡혀야 함
                _StubMeta("무상증자결정"),
                _StubMeta("정기주주총회소집공고"),  # rare_types 밖 — 안 셈
            ]
        )

        score = select_corpus_companies._rare_event_score(
            client, "C1", {"유상증자", "무상증자"}, years=3
        )

        assert score == 2

    def test_no_rare_type_hits_scores_zero(self) -> None:
        client = self._StubClient([_StubMeta("정기주주총회소집공고")])

        score = select_corpus_companies._rare_event_score(client, "C1", {"유상증자"}, years=3)

        assert score == 0

    def test_network_failure_scores_zero_instead_of_raising(self) -> None:
        """선정 단계에서 회사 하나 조회가 실패했다고 전체가 죽으면 안 된다(다른 A10
        스크립트들과 같은 원칙)."""

        class _FailingClient:
            def list_disclosures(self, corp_code: str, *, bgn_de: str, end_de: str) -> list:
                raise httpx.ConnectError("down")

        score = select_corpus_companies._rare_event_score(
            _FailingClient(), "C1", {"유상증자"}, years=3
        )

        assert score == 0


class TestRefreshCacheFlag:
    def test_refresh_cache_deletes_existing_cache_before_fetch(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        cache_path = tmp_path / "corp_code.zip"
        cache_path.write_bytes(b"stale content")

        _run_main(monkeypatch, tmp_path, extra_args=["--refresh-cache"])

        # _CacheAwareFakeClient는 캐시가 없을 때만 "새로 받는다" — 삭제됐다가 다시
        # 채워졌다는 뜻.
        assert cache_path.read_bytes() == b"fresh"

    def test_without_refresh_cache_flag_existing_cache_is_kept(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        cache_path = tmp_path / "corp_code.zip"
        cache_path.write_bytes(b"kept content")

        _run_main(monkeypatch, tmp_path)

        assert cache_path.read_bytes() == b"kept content"

    def test_refresh_cache_flag_is_noop_when_no_cache_exists(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        out_path = _run_main(monkeypatch, tmp_path, extra_args=["--refresh-cache"])

        selected = json.loads(out_path.read_text(encoding="utf-8"))
        assert {c["corp_code"] for c in selected} == {"C1"}
