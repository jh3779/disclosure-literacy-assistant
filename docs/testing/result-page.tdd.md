# 조회 결과 전용 페이지 — 구현·검증 기록

2026-09-23. 사용자 요청: “공시 루머 조회시 결과 화면이 이상함 차라리 결과 화면으로 이동하면 좋을듯 함”.

> 이 기록은 최초 페이지 분리 시점의 검증이다. 후속 작업에서는 조회 시작 즉시 결과 페이지로
> 이동하도록 개선했다. 현재 대기·근거·모바일 동작은 [디자인 리뷰 반영 기록](design-review.tdd.md)을 따른다.

조회 완료 시 `/result`로 이동해 결과를 맨 위부터 보여준다. 입력 페이지에는 CRT·입력·사전,
결과 페이지에는 조회 기준·사실 대조·맥락·복귀 버튼을 배치한다. 화면 계약은
[03_screen_contract.md](../03_screen_contract.md)의 SCR-001·SCR-003을 따른다.

## 변경과 상태 보존

- 성공·빈 결과·샘플·오류 모두 결과 페이지에서 보여준다. 잘못된 입력은 입력 페이지에서 안내한다.
- 다시 조회하기로 돌아오면 종목·문장·예시 선택을 복원한다. `_QueryDraft`를 위젯 키와 별도로
  저장해 페이지 이동 중 위젯 상태가 정리돼도 사용자가 작성한 초안이 남는다.
- 직전 결과 보기는 저장된 결과를 다시 연다. 초안과 조회 기준이 달라졌으면 이전 결과 안내를 붙인다.
- 새 세션에서 결과 URL에 직접 접근하면 결과 없음 안내와 복귀 버튼을 제공한다.
- 복귀·직전 결과 버튼에 크림색 배경과 진한 글자를 지정해 어두운 배경에서도 읽을 수 있게 했다.

## TDD 근거

| 단계 | 명령·결과 | 보장 |
| --- | --- | --- |
| RED | 신규 결과 이동·왕복·결과 없음 3건 → **3 failed, 73 deselected** | 기존 인라인 결과 화면이 새 화면 계약을 충족하지 못함 |
| GREEN | `.venv/bin/python -m pytest tests/test_demo_app.py -q` → **76 passed**, 28.38초 | 기존 동작 및 신규 페이지 이동·초안 복원 유지 |
| 전체 회귀 | 아래 coverage run → **548 passed, 12 skipped, 8 deselected**, 45.47초 | 저장소 기본 테스트 범위 통과. 기존 API·느린 모델 제외 조건 유지 |
| 커버리지 | demo 전체 **87%**, `app.py` **87%**, `terminal.py` **100%** | 분기 포함 측정 |
| 코드 검사 | Ruff check / format check, `git diff --check` | 통과 |
| 문서 검사 | markdownlint-cli2, 변경 UI 문서·검증 기록 6개 | 통과 |

RED 체크포인트: `a28ff34`. GREEN 체크포인트: `193756c`.

```bash
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 uv run --with coverage python -m coverage run \
  --data-file=/private/tmp/retro-ui/result-page.coverage \
  --source=disclosure_lit.demo --branch -m pytest -q
uv run --with coverage python -m coverage report \
  --data-file=/private/tmp/retro-ui/result-page.coverage -m
```

테스트에서 조회 함수를 실패하도록 교체한 후 페이지 왕복을 수행해, 직전 결과 재열람이 새
조회를 실행하지 않는 것도 확인했다. 입력 문장을 수정하고 결과를 다시 열었다 돌아와도
수정한 초안이 남는 경로를 포함한다.

Streamlit 1.63 AppTest는 프로그램의 `st.switch_page()` 이후 현재 페이지 해시를 다음
실행에 반영하지 않는다. 테스트의 `_NavigationAppTest`는 실제 navigation 메시지의 해시를
보존한다. `AppTest.from_file()`은 기본 클래스 인스턴스를 반환하므로 하위 클래스 생성자를
직접 사용한다. 이 보정은 테스트에만 있으며 제품 코드에 테스트 전용 상태를 넣지 않았다.
초기에 AppTest의 파일 경로 전용 `switch_page()`에 URL 경로를 넘긴 테스트 작성 오류는
수정한 뒤 위 RED를 기록했다.

## 실제 브라우저 검증

CUA로 Chrome에서 `tests/fixtures/terminal_preview.py`의 고정 데이터를 사용했다. 실제
`demo/app.py`를 실행하고 외부 코퍼스·DART·LLM만 대체한다. 아래 공시 내용은 UI 검증용이다.

| 경로·화면 | 확인 결과 |
| --- | --- |
| 삼성전자 선택 → 예시 → 확인 | URL `/result`, 화면 상단의 결과 제목·조회 기준·사실 대조 표시, CRT 없음 |
| 다시 조회하기 | URL `/`, 선택 종목·문장·예시 선택·전원 상태 복원 |
| 문장 수정 → 직전 결과 보기 → 복귀 | 원래 조회 기준·이전 결과 안내 표시, 수정한 문장 유지 |
| 의도한 타임아웃 입력 → 확인 | `/result`에서 사용자 오류 안내·접힌 기술적 상세·복귀 버튼 표시 |
| 새 세션에서 `/result` 직접 진입 | 저장 결과 없음 안내, 다시 조회하기로 입력 화면 복귀 |
| 데스크톱 결과 | 밝은 출력물, 사실 두 열, 진한 복귀 버튼 글자 확인 |
| 400 × 860 결과 | 사실 컬럼 세로 배치, 출처·맥락 안내까지 확인. viewport·document·body 너비 모두 400px |

반응형 검증 후 화면 크기 설정을 복원했다. 기존 `scripts/check_terminal_ui.py`도 결과 페이지
이동·입력 복원 흐름에 맞췄지만 이번에는 실행하지 않았다. 위 표는 CUA로 확인한 결과다.
기존 [terminal-ui.tdd.md](terminal-ui.tdd.md)의 이미지·자동 측정 JSON은 결과 페이지 분리 전 자료다.

## 범위와 한계

- 결과는 세션에 보관한다. `/result`는 영구 저장 또는 공유 링크가 아니며 새 세션에서는 재조회가 필요하다.
- 실제 LLM 조회 품질·응답시간, Safari·실제 모바일 기기는 이번에 다시 검사하지 않았다.
- AppTest 보정은 Streamlit 내부 테스트 API를 사용하므로 버전 변경 시 확인해야 한다.
- 기존 8501 서버는 조회 세션 보존을 위해 유지하고 최신 본 앱 미리보기는 8504에서 실행했다.

페이지 전환은 공식 [st.navigation](https://docs.streamlit.io/develop/api-reference/navigation/st.navigation)·
[st.switch_page](https://docs.streamlit.io/develop/api-reference/navigation/st.switch_page) API를 사용하며,
설치된 Streamlit 1.63 소스와 실제 브라우저에서 확인했다.
