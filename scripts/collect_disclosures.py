"""종목 하나 기준 최근 공시 일괄 수집 — Phase 1 A8. 담당: A(이승노).

DartClient.list_disclosures(A3) 로 목록을 받아, 각 공시를 fetch_document(A4)로
다운로드하고 chunk_disclosure(A6)로 청킹해서 data/raw/<corp_code>/ 아래에 저장한다.

저장 형식:
  data/raw/<corp_code>/documents/<rcept_no>.zip  — 원문 캐시(fetch_document의 cache_path)
  data/raw/<corp_code>/sections.jsonl            — DisclosureSection 한 줄에 하나씩(append)

재실행 안전: 원문은 이미 캐시돼 있으면 재다운로드하지 않는다(A4의 cache_path 재사용).
sections.jsonl 은 이미 처리된 rcept_no 를 건너뛴다(청킹 로직이 바뀌면 --force 로 재처리).

실패 처리: 공시 하나가 실패해도(네트워크 오류, 파싱 실패 등) 배치 전체를 죽이지 않고
건너뛰며 기록한다 — normalize_form_type/chunk_disclosure 의 "조용히 안 버림" 원칙과
같은 방향이지만, 여기서는 "그 공시 하나를 건너뛰고 계속 간다"는 의미로 적용한다.

실행:
    uv run python scripts/collect_disclosures.py 00126380 --days 90
    uv run python scripts/collect_disclosures.py 00126380 --bgn-de 20260101 --end-de 20260911
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from datetime import date, timedelta
from pathlib import Path

from disclosure_lit.config import settings
from disclosure_lit.dart.client import DartApiError, DartClient
from disclosure_lit.dart.ingest import DisclosureSection, chunk_disclosure

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def fix_console_encoding() -> None:
    """Windows 콘솔(cp949 등)에서 em dash·한글 출력 시 UnicodeEncodeError 나는 것 방지.

    A10(build_corpus.py, select_corpus_companies.py)도 같은 이유로 이 함수를 쓴다 —
    이 스크립트가 A10 오케스트레이터에 이미 임포트되고 있어서 여기 공용으로 둔다.
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("corp_code", help="DART corp_code (8자리, 예: 삼성전자 00126380)")
    parser.add_argument("--days", type=int, default=90, help="오늘부터 며칠 전까지 조회(기본 90)")
    parser.add_argument("--bgn-de", help="조회 시작일 YYYYMMDD (지정 시 --days 무시)")
    parser.add_argument("--end-de", help="조회 종료일 YYYYMMDD (기본 오늘)")
    parser.add_argument("--out-dir", default=None, help="저장 위치(기본 data/raw/<corp_code>/)")
    parser.add_argument(
        "--force", action="store_true", help="이미 처리된 rcept_no 도 다시 청킹해서 저장"
    )
    return parser.parse_args()


def _date_range(args: argparse.Namespace) -> tuple[str, str]:
    if args.bgn_de and args.end_de:
        return args.bgn_de, args.end_de
    end = date.today()
    bgn = end - timedelta(days=args.days)
    return bgn.strftime("%Y%m%d"), end.strftime("%Y%m%d")


def _load_existing_lines_by_rcept(sections_path: Path) -> dict[str, list[str]]:
    """sections_path 전체를 딱 한 번 읽어 rcept_no -> 그 rcept_no의 원본 JSON 줄
    목록으로 묶는다(Issue #46).

    예전에는 이미 처리된 rcept_no 집합만 구하는 `_load_processed_rcept_nos`와, 그
    rcept_no의 실제 줄 내용을 다시 읽어 걸러내는 `_replace_rcept_sections`가 따로
    있어서 --force 재처리 대상마다(즉 루프를 돌 때마다) 파일 전체를 다시 읽고 모든
    줄을 `json.loads`로 파싱했다 — 회사 하나에 이미 300줄이 쌓인 상태에서 신규 60건이
    들어오면 약 18,000회의 `json.loads` 호출이 발생하는 식으로, 누적 건수가 클수록
    호출당 비용이 비례해서 커졌다(회사당 O(누적건수²)에 가까움). 이 함수로 한 번만
    읽어서 재사용하면 O(누적건수)로 끝난다.
    """
    if not sections_path.exists():
        return {}
    grouped: dict[str, list[str]] = {}
    with sections_path.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            rcept_no = json.loads(line)["rcept_no"]
            grouped.setdefault(rcept_no, []).append(line if line.endswith("\n") else line + "\n")
    return grouped


def _sections_to_lines(sections: list[DisclosureSection]) -> list[str]:
    return [
        json.dumps(dataclasses.asdict(section), ensure_ascii=False) + "\n" for section in sections
    ]


def _append_sections(sections_path: Path, sections: list[DisclosureSection]) -> None:
    """신규 rcept_no의 섹션들을 파일 끝에 그대로 추가한다(Issue #46) — 기존 내용을
    읽거나 다시 쓰지 않아 O(1) I/O로 끝난다.

    `force=False` 기본 경로에서만 쓴다: `already_done`이 이미 기존 rcept_no를
    걸러내므로, 이 경로로 들어오는 rcept_no는 파일에 아직 없다는 게 보장된다 — 매
    공시 처리 직후 바로 디스크에 반영되므로(collect_for_company 의 force=False 분기
    참고) 중간에 끊겨도 재실행하면 이어서 진행되는 재실행 안전성이 그대로 유지된다.
    """
    with sections_path.open("a", encoding="utf-8") as f:
        f.writelines(_sections_to_lines(sections))


def _write_grouped_sections_jsonl(
    sections_path: Path, lines_by_rcept: dict[str, list[str]]
) -> None:
    """rcept_no별로 묶인 줄들을 파일 하나로 한 번에 쓴다(--force 전용, Issue #46).

    `--force` 재처리는 기존 rcept_no의 줄을 교체해야 해서(PR #31 리뷰 지적,
    @9abin/@jh3779 — 단순 append 로는 재처리가 "교체"가 아니라 "누적"이 됨) 단순
    append로는 안 된다. 대신 `_load_existing_lines_by_rcept()`로 전체를 메모리에
    한 번만 올려두고, `collect_for_company()`가 루프를 도는 동안 딕셔너리만 갱신하다가
    이 함수로 끝나면 한 번만 쓴다 — 회사당 파일 전체를 재처리 건수만큼 반복해서
    읽고·파싱하고·쓰던 예전 방식과 달리 여기서는 디스크 쓰기가 정확히 1번이다.
    (트레이드오프: `--force` 실행 도중 끊기면 이번 실행분은 전부 유실된다 — `--force`는
    이미 처리된 것도 전부 다시 받는 명시적·일회성 작업이라 감내할 만한 트레이드오프로
    판단했다. 기본 경로(`--force` 없음)는 `_append_sections()`가 그대로 매 건마다
    디스크에 반영해 재실행 안전성을 유지한다.)
    """
    with sections_path.open("w", encoding="utf-8") as f:
        for lines in lines_by_rcept.values():
            f.writelines(lines)


@dataclasses.dataclass
class FailureRecord:
    """실패한 공시 하나의 상세(Issue #45) — corp_code·rcept_no·report_nm·예외 내용.

    `verbose` 콘솔 출력과 달리 `collect_for_company()`가 항상 채운다. A10 배치
    (`build_corpus.py`)는 `verbose=False`로 호출하므로, 예전에는 이 정보가 어디에도
    안 남고 `CollectionStats.failed` 숫자만 늘었다 — 100개사를 몇 시간 돌린 뒤
    "실패 12건"만 보고 원인 파악이 불가능했다."""

    corp_code: str
    rcept_no: str
    report_nm: str
    error: str


def write_failures_jsonl(path: Path, failures: list[FailureRecord]) -> None:
    """실패 상세를 JSONL로 항상 남긴다(Issue #45) — verbose 콘솔 출력(스크롤에 묻혀
    사라짐)과 달리 배치가 끝난 뒤에도 파일로 남아 추적할 수 있다. 실패가 없으면 이전
    실행의 stale한 파일이 남아있지 않도록 지운다(재실행 안전성 원칙과 동일한 이유)."""
    if not failures:
        path.unlink(missing_ok=True)
        return
    lines = [json.dumps(dataclasses.asdict(f), ensure_ascii=False) for f in failures]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@dataclasses.dataclass
class CollectionStats:
    corp_code: str
    ok: int
    skipped: int
    failed: int
    total_disclosures: int
    out_dir: Path
    sections_path: Path
    failures: list[FailureRecord] = dataclasses.field(default_factory=list)


def collect_for_company(
    client: DartClient,
    corp_code: str,
    *,
    bgn_de: str,
    end_de: str,
    out_dir: Path | None = None,
    force: bool = False,
    verbose: bool = True,
) -> CollectionStats:
    """종목 하나의 공시를 수집해 data/raw/<corp_code>/ 에 저장한다.

    이 스크립트(A8, CLI)와 A10 다종목 오케스트레이터(scripts/build_corpus.py)가
    공유하는 핵심 로직이라 함수로 뺐다 — 종목 100개를 순회할 때 이 로직을 복붙하지
    않기 위함.
    """
    resolved_out_dir = out_dir or PROJECT_ROOT / "data" / "raw" / corp_code
    doc_cache_dir = resolved_out_dir / "documents"
    sections_path = resolved_out_dir / "sections.jsonl"
    resolved_out_dir.mkdir(parents=True, exist_ok=True)
    doc_cache_dir.mkdir(parents=True, exist_ok=True)

    if verbose:
        print(f"[{corp_code}] 공시 목록 조회 중 ({bgn_de}~{end_de})...")
    metas = client.list_disclosures(corp_code, bgn_de=bgn_de, end_de=end_de)
    if verbose:
        print(f"[{corp_code}] 공시 {len(metas)}건 발견")

    existing_by_rcept = _load_existing_lines_by_rcept(sections_path)
    already_done = set() if force else set(existing_by_rcept.keys())
    if already_done and verbose:
        print(f"이미 처리된 {len(already_done)}건은 건너뜀 (--force 로 강제 재처리 가능)")

    # --force 는 기존 rcept_no를 교체해야 해서 in-memory로 모았다가 루프가 끝난 뒤
    # 한 번만 다시 쓴다 — 기본 경로는 신규 rcept_no만 처리하므로 바로 append 한다
    # (Issue #46, 각 헬퍼 docstring 참고).
    updated_by_rcept = dict(existing_by_rcept) if force else None

    ok, skipped, failed = 0, 0, 0
    failures: list[FailureRecord] = []
    for meta in metas:
        if meta.rcept_no in already_done:
            skipped += 1
            continue
        try:
            cache_path = doc_cache_dir / f"{meta.rcept_no}.zip"
            raw = client.fetch_document(meta.rcept_no, cache_path=cache_path)
            sections = chunk_disclosure(
                raw, rcept_no=meta.rcept_no, corp_code=meta.corp_code, rcept_dt=meta.rcept_dt
            )
        except DartApiError as exc:
            failed += 1
            failures.append(
                FailureRecord(
                    corp_code=corp_code,
                    rcept_no=meta.rcept_no,
                    report_nm=meta.report_nm,
                    error=str(exc),
                )
            )
            if verbose:
                print(f"  실패 [{meta.rcept_no}] {meta.report_nm}: {exc}", file=sys.stderr)
            continue
        except Exception as exc:  # noqa: BLE001 - 배치 전체를 죽이지 않고 계속 간다
            failed += 1
            failures.append(
                FailureRecord(
                    corp_code=corp_code,
                    rcept_no=meta.rcept_no,
                    report_nm=meta.report_nm,
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
            if verbose:
                print(
                    f"  실패 [{meta.rcept_no}] {meta.report_nm}: {type(exc).__name__}: {exc}",
                    file=sys.stderr,
                )
            continue

        if updated_by_rcept is not None:
            updated_by_rcept[meta.rcept_no] = _sections_to_lines(sections)
        else:
            _append_sections(sections_path, sections)
        ok += 1

    if updated_by_rcept is not None:
        _write_grouped_sections_jsonl(sections_path, updated_by_rcept)

    return CollectionStats(
        corp_code=corp_code,
        ok=ok,
        skipped=skipped,
        failed=failed,
        total_disclosures=len(metas),
        out_dir=resolved_out_dir,
        sections_path=sections_path,
        failures=failures,
    )


def main() -> None:
    fix_console_encoding()
    args = _parse_args()
    bgn_de, end_de = _date_range(args)
    out_dir = Path(args.out_dir) if args.out_dir else None
    client = DartClient(settings.dart_api_key)

    stats = collect_for_company(
        client, args.corp_code, bgn_de=bgn_de, end_de=end_de, out_dir=out_dir, force=args.force
    )
    failures_path = stats.out_dir / "failures.jsonl"
    write_failures_jsonl(failures_path, stats.failures)

    print(
        f"\n[{stats.corp_code}] 완료 — 성공 {stats.ok}건, 건너뜀 {stats.skipped}건, "
        f"실패 {stats.failed}건"
        f"\n원문 캐시: {stats.out_dir / 'documents'}"
        f"\n섹션 저장: {stats.sections_path}"
    )
    if stats.failures:
        print(f"실패 상세: {failures_path}")


if __name__ == "__main__":
    main()
