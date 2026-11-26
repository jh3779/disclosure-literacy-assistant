"""scripts/collect_disclosures.py(A8) — 공시 단위 실패 상세 기록(Issue #45) +
sections.jsonl O(1) append(Issue #46).

PR #41 리뷰 후 발견(@jh3779), 두 이슈가 같은 `collect_for_company()`를 건드린다:

- **Issue #45**: `build_corpus.py`가 `collect_for_company()`를 항상 `verbose=False`로
  호출해서, 개별 공시 실패(DartApiError·파싱 오류 등)의 상세가 `CollectionStats.failed`
  숫자로만 남고 어느 회사·공시·무슨 예외였는지는 어디에도 기록되지 않았다.
  `FailureRecord`/`write_failures_jsonl()`이 verbose 여부와 무관하게 항상 상세를
  남기는지 확인한다.
- **Issue #46**: `_replace_rcept_sections()`가 새 공시를 처리할 때마다 `sections.jsonl`
  전체를 읽고·모든 줄을 `json.loads`로 파싱하고·다시 썼다 — 회사당 누적 건수가
  클수록 신규 1건 처리 비용이 커져 O(누적건수²)에 가까웠다. 기본 경로(`--force`
  없음, 신규 rcept_no만 처리)는 append 로 바꾸고, `--force`(기존 rcept_no 교체
  필요)만 전체를 한 번 읽고 한 번 쓰도록 고쳤다 — 그 동작을 검증한다.

scripts/ 는 패키지가 아니라서(`python scripts/collect_disclosures.py` 로 실행)
test_build_corpus.py 와 같은 방식으로 sys.path 에 scripts/ 를 넣고 모듈로 임포트한다.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import collect_disclosures  # noqa: E402
from collect_disclosures import (  # noqa: E402
    FailureRecord,
    collect_for_company,
    write_failures_jsonl,
)

from disclosure_lit.dart.client import DartApiError, DisclosureMeta  # noqa: E402
from disclosure_lit.dart.ingest import DisclosureSection  # noqa: E402


class _FakeClient:
    def __init__(self, metas: list[DisclosureMeta]) -> None:
        self._metas = metas

    def list_disclosures(self, corp_code: str, *, bgn_de: str, end_de: str) -> list[DisclosureMeta]:
        return self._metas

    def fetch_document(self, rcept_no: str, *, cache_path: Path | None = None) -> bytes:
        return b"<xml/>"


def _meta(rcept_no: str, *, corp_code: str = "C1", report_nm: str = "MOU 체결") -> DisclosureMeta:
    return DisclosureMeta(
        rcept_no=rcept_no,
        corp_code=corp_code,
        corp_name="회사",
        report_nm=report_nm,
        rcept_dt="20260101",
    )


def _line(rcept_no: str, text: str = "old") -> str:
    return json.dumps({"rcept_no": rcept_no, "corp_code": "C1", "text": text}) + "\n"


def _section(rcept_no: str, corp_code: str, text: str) -> DisclosureSection:
    return DisclosureSection(
        rcept_no=rcept_no, corp_code=corp_code, form_type="MOU", section_title="", text=text
    )


class TestFailureRecordPopulation:
    def test_dart_api_error_recorded_even_when_not_verbose(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        meta = _meta("R1", report_nm="유상증자 결정")

        def raiser(*args: object, **kwargs: object) -> None:
            raise DartApiError("800", "시스템 점검중")

        monkeypatch.setattr(collect_disclosures, "chunk_disclosure", raiser)

        stats = collect_for_company(
            _FakeClient([meta]),
            "C1",
            bgn_de="20260101",
            end_de="20260201",
            out_dir=tmp_path,
            verbose=False,
        )

        assert stats.failed == 1
        assert stats.failures == [
            FailureRecord(
                corp_code="C1",
                rcept_no="R1",
                report_nm="유상증자 결정",
                error="DART API 오류(status=800): 시스템 점검중",
            )
        ]

    def test_unexpected_exception_recorded_with_type_name(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        meta = _meta("R1")

        def raiser(*args: object, **kwargs: object) -> None:
            raise ValueError("파싱 실패")

        monkeypatch.setattr(collect_disclosures, "chunk_disclosure", raiser)

        stats = collect_for_company(
            _FakeClient([meta]),
            "C1",
            bgn_de="20260101",
            end_de="20260201",
            out_dir=tmp_path,
            verbose=False,
        )

        assert stats.failures[0].error == "ValueError: 파싱 실패"

    def test_success_and_failure_mixed(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        ok_meta = _meta("R-OK")
        fail_meta = _meta("R-FAIL")

        def selective_chunk(
            raw: bytes, *, rcept_no: str, corp_code: str, rcept_dt: str
        ) -> list[object]:
            if rcept_no == "R-FAIL":
                raise DartApiError("800", "실패")
            return []

        monkeypatch.setattr(collect_disclosures, "chunk_disclosure", selective_chunk)

        stats = collect_for_company(
            _FakeClient([ok_meta, fail_meta]),
            "C1",
            bgn_de="20260101",
            end_de="20260201",
            out_dir=tmp_path,
            verbose=False,
        )

        assert stats.ok == 1
        assert stats.failed == 1
        assert [f.rcept_no for f in stats.failures] == ["R-FAIL"]


class TestWriteFailuresJsonl:
    def test_writes_one_json_line_per_failure(self, tmp_path: Path) -> None:
        path = tmp_path / "failures.jsonl"
        failures = [
            FailureRecord(corp_code="C1", rcept_no="R1", report_nm="A", error="err1"),
            FailureRecord(corp_code="C2", rcept_no="R2", report_nm="B", error="err2"),
        ]

        write_failures_jsonl(path, failures)

        lines = path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0]) == {
            "corp_code": "C1",
            "rcept_no": "R1",
            "report_nm": "A",
            "error": "err1",
        }

    def test_empty_failures_removes_stale_file(self, tmp_path: Path) -> None:
        """재실행 안전성과 같은 원칙 — 이전 실행에서 실패가 있었다가 이번엔 전부
        성공하면, 예전 failures.jsonl이 그대로 남아 착시를 주면 안 된다."""
        path = tmp_path / "failures.jsonl"
        path.write_text("stale\n", encoding="utf-8")

        write_failures_jsonl(path, [])

        assert not path.exists()

    def test_empty_failures_noop_when_file_absent(self, tmp_path: Path) -> None:
        path = tmp_path / "failures.jsonl"

        write_failures_jsonl(path, [])  # 존재하지 않아도 에러 없이 통과

        assert not path.exists()


class TestAppendOnlyDefaultPath:
    def test_new_rcept_no_appends_without_touching_existing_lines(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        out_dir = tmp_path / "C1"
        out_dir.mkdir()
        sections_path = out_dir / "sections.jsonl"
        sections_path.write_text(_line("R-OLD"), encoding="utf-8")

        monkeypatch.setattr(
            collect_disclosures,
            "chunk_disclosure",
            lambda raw, *, rcept_no, corp_code, rcept_dt: [_section(rcept_no, corp_code, "new")],
        )

        stats = collect_for_company(
            _FakeClient([_meta("R-NEW")]),
            "C1",
            bgn_de="20260101",
            end_de="20260201",
            out_dir=out_dir,
        )

        assert stats.ok == 1
        lines = sections_path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["rcept_no"] == "R-OLD"  # 기존 줄 순서·내용 그대로
        assert json.loads(lines[1])["rcept_no"] == "R-NEW"

    def test_only_load_existing_lines_called_once_regardless_of_new_count(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Issue #46 핵심 — 신규 공시가 몇 건이든 전체 파일을 읽는 건 딱 1번이어야
        한다(예전엔 신규 건수만큼 반복해서 읽었다)."""
        out_dir = tmp_path / "C1"
        out_dir.mkdir()
        sections_path = out_dir / "sections.jsonl"
        sections_path.write_text(_line("R-OLD"), encoding="utf-8")

        load_calls = []
        original_load = collect_disclosures._load_existing_lines_by_rcept

        def spy_load(path: Path) -> dict:
            load_calls.append(path)
            return original_load(path)

        monkeypatch.setattr(collect_disclosures, "_load_existing_lines_by_rcept", spy_load)
        monkeypatch.setattr(
            collect_disclosures,
            "chunk_disclosure",
            lambda raw, *, rcept_no, corp_code, rcept_dt: [
                _section(rcept_no, corp_code, "새 공시")
            ],
        )

        metas = [_meta(f"R-NEW-{i}") for i in range(5)]
        collect_for_company(
            _FakeClient(metas), "C1", bgn_de="20260101", end_de="20260201", out_dir=out_dir
        )

        assert len(load_calls) == 1

    def test_write_grouped_not_called_on_default_path(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        out_dir = tmp_path / "C1"
        out_dir.mkdir()

        write_calls = []
        monkeypatch.setattr(
            collect_disclosures,
            "_write_grouped_sections_jsonl",
            lambda *a, **k: write_calls.append(1),
        )
        monkeypatch.setattr(
            collect_disclosures,
            "chunk_disclosure",
            lambda raw, *, rcept_no, corp_code, rcept_dt: [
                _section(rcept_no, corp_code, "새 공시")
            ],
        )

        collect_for_company(
            _FakeClient([_meta("R1")]),
            "C1",
            bgn_de="20260101",
            end_de="20260201",
            out_dir=out_dir,
        )

        assert write_calls == []


class TestForceReplacesInOnePass:
    def test_force_replaces_existing_rcept_content(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """PR #31 리뷰 지적 회귀 확인 — force 재처리가 "누적"이 아니라 "교체"여야 한다."""
        out_dir = tmp_path / "C1"
        out_dir.mkdir()
        sections_path = out_dir / "sections.jsonl"
        sections_path.write_text(_line("R1", text="옛날 내용"), encoding="utf-8")

        monkeypatch.setattr(
            collect_disclosures,
            "chunk_disclosure",
            lambda raw, *, rcept_no, corp_code, rcept_dt: [
                _section(rcept_no, corp_code, "새 내용")
            ],
        )

        collect_for_company(
            _FakeClient([_meta("R1")]),
            "C1",
            bgn_de="20260101",
            end_de="20260201",
            out_dir=out_dir,
            force=True,
        )

        lines = sections_path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1  # 누적(2줄)이 아니라 교체(1줄)
        assert json.loads(lines[0])["text"] == "새 내용"

    def test_force_preserves_untouched_rcept_not_in_this_run(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """이번 실행의 metas 에 없는(예: 기간 밖으로 벗어난) 기존 rcept_no는 force
        재처리에서도 그대로 보존돼야 한다 — 전체를 날리고 새로 쓰는 게 아니다."""
        out_dir = tmp_path / "C1"
        out_dir.mkdir()
        sections_path = out_dir / "sections.jsonl"
        sections_path.write_text(_line("R-UNTOUCHED", text="유지됨"), encoding="utf-8")

        monkeypatch.setattr(
            collect_disclosures,
            "chunk_disclosure",
            lambda raw, *, rcept_no, corp_code, rcept_dt: [
                _section(rcept_no, corp_code, "새로 처리됨")
            ],
        )

        # 이번 실행의 metas 에는 R-UNTOUCHED 가 없다(예: list_disclosures 기간이 달라짐).
        collect_for_company(
            _FakeClient([_meta("R-NEW")]),
            "C1",
            bgn_de="20260101",
            end_de="20260201",
            out_dir=out_dir,
            force=True,
        )

        lines = sections_path.read_text(encoding="utf-8").splitlines()
        rcept_nos = {json.loads(line)["rcept_no"] for line in lines}
        assert rcept_nos == {"R-UNTOUCHED", "R-NEW"}

    def test_write_grouped_called_exactly_once_for_multiple_reprocessed(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Issue #46 핵심 — force 재처리 건수가 몇 건이든 전체 파일을 쓰는 건 딱
        1번이어야 한다(예전엔 재처리 건수만큼 반복해서 다시 썼다)."""
        out_dir = tmp_path / "C1"
        out_dir.mkdir()
        sections_path = out_dir / "sections.jsonl"
        sections_path.write_text("".join(_line(f"R{i}") for i in range(3)), encoding="utf-8")

        write_calls = []
        original_write = collect_disclosures._write_grouped_sections_jsonl

        def spy_write(path: Path, grouped: dict) -> None:
            write_calls.append(1)
            original_write(path, grouped)

        monkeypatch.setattr(collect_disclosures, "_write_grouped_sections_jsonl", spy_write)
        monkeypatch.setattr(
            collect_disclosures,
            "chunk_disclosure",
            lambda raw, *, rcept_no, corp_code, rcept_dt: [_section(rcept_no, corp_code, "재처리")],
        )

        metas = [_meta(f"R{i}") for i in range(3)]
        collect_for_company(
            _FakeClient(metas),
            "C1",
            bgn_de="20260101",
            end_de="20260201",
            out_dir=out_dir,
            force=True,
        )

        assert len(write_calls) == 1

    def test_failed_reprocess_does_not_lose_original_content(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """force 재처리가 실패하면(네트워크 오류 등) 기존 데이터는 보존돼야 한다
        (PR #31 원칙 — 기존 _replace_rcept_sections 의 "성공한 뒤에만 교체" 그대로)."""
        out_dir = tmp_path / "C1"
        out_dir.mkdir()
        sections_path = out_dir / "sections.jsonl"
        sections_path.write_text(_line("R1", text="원본"), encoding="utf-8")

        def failing_chunk(raw: bytes, *, rcept_no: str, corp_code: str, rcept_dt: str):
            raise ValueError("파싱 실패")

        monkeypatch.setattr(collect_disclosures, "chunk_disclosure", failing_chunk)

        collect_for_company(
            _FakeClient([_meta("R1")]),
            "C1",
            bgn_de="20260101",
            end_de="20260201",
            out_dir=out_dir,
            force=True,
        )

        lines = sections_path.read_text(encoding="utf-8").splitlines()
        assert json.loads(lines[0])["text"] == "원본"
