"""DART OpenAPI 연결 스모크 테스트 — Phase 1 A1. 담당: A(이승노).

`.env` 의 `DART_API_KEY` 가 실제로 발급된 유효한 키인지, opendart.fss.or.kr 이 응답하는지
가장 얇게 확인한다. 공시 목록 조회(list.json) 한 번을 쏴 보고 status 코드를 해석해 준다.

실행:
    uv run python scripts/dart_smoke.py                # 최근 7일 코스피 공시 목록
    uv run python scripts/dart_smoke.py 00126380       # 특정 회사(예: 삼성전자) 최근 공시

L0 클라이언트(`disclosure_lit/dart/client.py`) 구현(A3/A4) 전까지 쓰는 임시 확인용 스크립트다.
클라이언트가 생기면 이 로직은 거기로 흡수되고 이 파일은 제거한다.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta

import httpx

LIST_URL = "https://opendart.fss.or.kr/api/list.json"

# DART 가 status 필드로 돌려주는 응답 코드 (개발가이드 공통 응답)
STATUS_MESSAGES = {
    "000": "정상",
    "010": "등록되지 않은 키",
    "011": "사용할 수 없는 키 (오픈API 이용활성화 필요)",
    "012": "접근할 수 없는 IP",
    "013": "조회된 데이터 없음",
    "020": "요청 제한 초과 (분당 1,000건)",
    "021": "조회 가능한 회사 개수 초과",
    "100": "필드의 부적절한 값",
    "101": "부적절한 접근",
    "800": "시스템 점검 중",
    "900": "정의되지 않은 오류",
    "901": "사용자 계정의 개인정보 보유기간 만료",
}


def _fix_console_encoding() -> None:
    """Windows 콘솔(cp949 등)에서 em dash·한글 출력 시 UnicodeEncodeError 나는 것 방지."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _load_api_key() -> str:
    from disclosure_lit.config import settings

    key = settings.dart_api_key.strip()
    if not key or key in {"...", "your-key-here"}:
        raise SystemExit(
            "DART_API_KEY 가 비어 있거나 자리표시자입니다. "
            "opendart.fss.or.kr 개인회원 가입 후 발급받은 40자리 키를 .env 에 넣으세요."
        )
    return key


def main() -> None:
    _fix_console_encoding()
    api_key = _load_api_key()

    corp_code = sys.argv[1] if len(sys.argv) > 1 else None
    end = date.today()
    bgn = end - timedelta(days=7 if corp_code is None else 90)

    params: dict[str, str] = {
        "crtfc_key": api_key,
        "bgn_de": bgn.strftime("%Y%m%d"),
        "end_de": end.strftime("%Y%m%d"),
        "page_no": "1",
        "page_count": "10",
    }
    scope = f"corp_code={corp_code}" if corp_code is not None else "corp_cls=Y"
    if corp_code is not None:
        params["corp_code"] = corp_code
    else:
        params["corp_cls"] = "Y"  # 코스피

    print(f"GET {LIST_URL}  ({params['bgn_de']}~{params['end_de']}, {scope})")

    try:
        resp = httpx.get(LIST_URL, params=params, timeout=10.0)
        resp.raise_for_status()
    except httpx.HTTPError as exc:  # 네트워크/HTTP 레벨 실패
        raise SystemExit(f"HTTP 요청 실패: {exc}") from exc

    body = resp.json()
    status = body.get("status", "?")
    label = STATUS_MESSAGES.get(status, "알 수 없는 코드")
    print(f"status={status} ({label})  message={body.get('message')!r}")

    if status == "000":
        rows = body.get("list", [])
        print(f"공시 {len(rows)}건 (total_count={body.get('total_count')}):")
        for row in rows[:5]:
            print(f"  {row.get('rcept_dt')}  {row.get('corp_name')}  {row.get('report_nm')}")
        print("\n연결 OK — DART_API_KEY 유효, list.json 응답 정상.")
        return

    if status == "013":
        print("\n키는 유효하지만 해당 조건에 공시가 없습니다. 날짜 범위를 넓혀 다시 시도하세요.")
        return

    raise SystemExit(f"\nDART 응답 오류(status={status}). .env 의 키/설정을 확인하세요.")


if __name__ == "__main__":
    main()
