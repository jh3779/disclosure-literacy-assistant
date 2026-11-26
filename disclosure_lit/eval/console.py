"""오프라인 평가 러너들이 공유하는 콘솔 출력 준비 — L5.

`run_eval.py`와 `l2b_holdout.py` 둘 다 진행 상황을 한글·em dash가 섞인 문장으로
stdout에 찍는다. 이 모듈이 있는 이유는 그 준비 작업을 **한 군데서만** 하기
위해서다 — 같은 함수를 러너마다 복사해두면 한쪽만 고쳐지고 다른 쪽은 조용히
옛날 동작으로 남는다(실제로 2026-09-22 PR #112 리뷰에서 `l2b_holdout.run()`이
이 처리를 빼먹은 채 병합될 뻔했다).

`scripts/`는 `collect_disclosures.fix_console_encoding()`을 같은 식으로 공유하지만
`disclosure_lit/`은 패키지가 아닌 `scripts/`에 의존하면 안 되므로 여기 따로 둔다.
"""

from __future__ import annotations

import sys


def fix_console_encoding() -> None:
    """Windows 콘솔(cp949 등)에서 한글·em dash 출력 시 UnicodeEncodeError 나는 것 방지.

    실측(run_eval.py 첫 실행) — 최종 요약 `print(f"전체 {n}건 — ...")`의 "—"에서
    UnicodeEncodeError로 죽었다. 케이스별 실패 로그(stderr)는 같은 "—"를 써도 안
    죽었는데, CPython이 stderr 기본 에러 핸들러로 backslashreplace를 쓰는 반면
    stdout은 strict라서다 — stdout도 같이 막아야 한다.

    **첫 출력보다 먼저** 불러야 한다. 러너들은 결과 요약뿐 아니라 진행 상황
    안내에도 em dash를 쓰므로, 진입점 맨 첫 줄에서 부르지 않으면 본작업이
    시작되기도 전에 죽는 경로가 남는다.
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
