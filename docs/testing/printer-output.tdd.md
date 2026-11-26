# 프린터 출력과 종목 선택 정리 — 검증 기록

2026-09-23. 사용자 제안: 결과를 프린트 용지가 나오는 형태로 표시하고 종목 선택의 삭제
표시를 제거한다. `design-review`의 animate 지침에 따라 새 결과 한 장의 등장만 연출한다.

## 구현

- 결과 출력물 위에 장식용 프린터 배출구를 추가했다. 새 조회 성공 시 종이를 아래로 드러내는
  `paperFeed`를 1초 동안 한 번 재생한다. 본문 요소별 지연과 인위적인 서버 대기는 없다.
- `_print_result_once`는 조회 성공 후 설정하고 첫 결과 렌더에서 소비한다. 오류·직전 결과
  보기·일반 재실행에는 출력 CSS를 주입하지 않는다. 빈 결과도 정상 조회 결과로 표시한다.
- 콘텐츠는 기본 상태에서 보인다. `prefers-reduced-motion: no-preference`일 때만 연출하며,
  키보드 포커스가 출력물 안에 들어오면 가림과 이동을 해제해 즉시 읽을 수 있게 한다.
  포커스 변화가 애니메이션 자체를 재시작하지 않도록 이름과 타이밍은 유지한다.
- 종목 선택의 `Clear value` 버튼만 `display: none`으로 숨긴다. 선택 전 placeholder,
  종목 검색·펼치기·변경은 유지한다. Streamlit 1.63 설치 소스에서 삭제 버튼이 `index=None`에
  묶여 있고 독립적인 공개 옵션이 없음을 확인했으므로 현재 DOM 안으로 CSS 범위를 제한했다.

## TDD 근거

RED `bd79155`: 신규 3개 테스트가 출력 연출 부재로 실패했다.
GREEN `8d218e1`: 같은 3개 테스트가 통과했다.

| 보장 | 검증 | 결과 |
| --- | --- | --- |
| 새 결과는 한 번 출력, 다음 새 조회에서는 다시 출력 | `TestPrinterOutput.test_new_result_feeds_once_and_next_lookup_feeds_again` | PASS |
| 직전 결과 보기는 연출 반복 없이 저장 결과 표시 | `TestPrinterOutput.test_reopening_saved_result_does_not_replay_feed` | PASS |
| 오류는 즉시 표시, 성공한 재시도는 출력 연출 | `TestPrinterOutput.test_error_is_immediate_and_successful_retry_feeds` | PASS |

```bash
.venv/bin/python -m pytest tests/test_demo_app.py -q -k TestPrinterOutput
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 uv run --with coverage python -m coverage run \
  --data-file=/private/tmp/printer-evidence/coverage \
  --source=disclosure_lit.demo --branch -m pytest -q
uv run --with coverage python -m coverage report \
  --data-file=/private/tmp/printer-evidence/coverage -m
```

- RED: **3 failed, 81 deselected**, 2.73초. GREEN: **3 passed, 81 deselected**, 2.77초.
- 전체: **556 passed, 12 skipped, 8 deselected**, 55.94초. UI 테스트 84개 포함.
- 분기 포함 커버리지: demo **88%**, `app.py` **88%**, `terminal.py` **100%**.
- Ruff check·format check 통과. 이후 제품 변경은 포커스·연출 종료 CSS 보정에 한정했다.

## 실제 화면 확인

CUA Chrome에서 `tests/fixtures/terminal_preview.py` 고정 데이터를 사용했다. 삭제(X)는
접근성 트리에서 빠지고 computed display가 `none`, 펼치기는 `flex`였다. 삼성전자에서
SK하이닉스로 변경한 뒤에도 선택 값이 유지되고 조회할 수 있었다.

새 조회 직후 `paperFeed`, duration `1s`와 진행 중 `translateY(-17.43px)`·부분 clip을
확인했다. 직전 결과 재열람과 오류는 animation `none`이었다. 데스크톱에서는 배출구 바로
아래 종이가 이어졌고, 320px 결과·오류의 문서 너비는 320px이었다. 배출구 좌우는
7.6~312.4px로 화면 안에 들어왔다. 임시 viewport는 검증 후 복원했다.

최종 보정 후에는 새 조회의 진행 중 clip·이동을 다시 확인했고, 근거 링크→Tab→Enter로
원문을 펼친 뒤 출력물 밖으로 포커스를 옮겨도 clip·transform이 `none`으로 유지되었다.
400px에서도 문서 너비는 400px, 새 검증 탭의 warn/error는 0건이었다.

`scripts/check_terminal_ui.py`는 캡처 전에 `paperFeed` 종료까지 기다리도록 보완했다.
이번 세션에서는 이 스크립트를 실행하지 않고 CUA로 확인했다. AppTest는 출력 CSS의 일회성
주입 경계를 검증하며 프레임·레이아웃 검증을 대신하지 않는다.

## 한계와 미리보기

- OS의 모션 축소 설정, 실제 스크린리더·모바일 기기, Safari는 이번에 직접 조작하지 않았다.
  모션 축소 분기는 CSS로 구현·확인했다. 실제 LLM 품질·응답시간은 재측정하지 않았다.
- Streamlit이 삭제 버튼의 접근성 이름·DOM을 바꾸면 숨김 선택자도 재검증해야 한다.
- 기존 사용자 조회 세션은 유지하고 최신 본 앱을 `http://127.0.0.1:8507/?power=on`에서 제공한다.
- 현재 화면 계약은 [03 화면](../03_screen_contract.md), [05 상태](../05_state_machine.md),
  [07 스타일](../07_visual_style.md)을 따른다.
