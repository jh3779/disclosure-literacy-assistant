# 레트로 공시 단말기 — 구현·검증 기록

2026-09-23. 사용자가 요청한 디자인 리터치를 입장·입력 영역에 적용했다.
현행 화면 계약은 [03_screen_contract.md](../03_screen_contract.md)를 따른다.
이 문서의 최초 구현·캡처는 결과 페이지 분리 전 기록이다. 후속 이동·복귀 검증은
[result-page.tdd.md](result-page.tdd.md)를 따른다.

## 변경

- 꺼진 CRT → 키보드로도 조작 가능한 전원 버튼 → 켜짐 1회 → 기존 조회 입력.
- `?power=on`으로 전원 켜짐 연출을 건너뛴다. 사전은 전원을 켜지 않아도 사용 가능하다.
- 결과·오류는 모니터 밖 밝은 출력물로 표시한다. 기존 결과 보존·조회 기준·예시 콜백을 유지한다.
- CSS와 전원 표시를 별도 모듈로 분리했다. Galmuri/Pretendard와 라이선스를 로컬에 포함했다.
- Streamlit 1.63의 DOM을 검증 기준으로 삼고 의존성 하한·잠금 파일을 맞췄다.
- 화면 흐름·계약·상태·스타일 문서를 현행 구현에 맞췄다. 초기 HTML 목업은 아카이브임을 표시했다.

## TDD 근거

| 단계 | 명령·결과 | 보장 |
| --- | --- | --- |
| RED | `.venv/bin/python -m pytest tests/test_demo_app.py -k TerminalEntry -q` → **3 failed, 2 passed, 67 deselected** | 기존 코드는 바로 입력을 표시하며 전원 버튼이 없어 실패. 사전·바로 입력 대조군 2개는 통과 |
| GREEN | `.venv/bin/python -m pytest tests/test_demo_app.py -q` → **72 passed** | 기존 67개 동작과 신규 5개 진입 검사 유지 |
| 전체 회귀 | `HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 .venv/bin/python -m pytest -q` → **544 passed, 12 skipped, 8 deselected** | 기본 테스트 범위의 회귀 없음. API·느린 모델 등의 제외 조건은 저장소 기본 설정 유지 |
| 커버리지 | `uv run --with coverage python -m coverage run --source=disclosure_lit.demo --branch -m pytest tests/test_demo_app.py -q` 후 `coverage report -m` | demo 전체 **83%**, 새 `terminal.py` **100%**. 측정 파일은 임시 경로에 보관 |
| 코드 검사 | Ruff check / format check | 통과 |
| 문서 검사 | markdownlint-cli2, 변경 Markdown 문서 | 통과 |

RED 체크포인트: `b3675d6`. GREEN 체크포인트: `3835cc5`.
초기 실패 중 사전 렌더링 형식을 잘못 가정한 테스트는 기존 Markdown 방식에 맞춰 고친 뒤,
위 RED 결과를 다시 확인하고 커밋했다. 이 테스트 작성 오류를 구현 실패로 세지 않았다.

## 실제 브라우저 검증

```bash
uv run --with playwright python -m playwright install chromium
uv run --with playwright python scripts/check_terminal_ui.py --output /tmp/terminal-ui
```

검사 스크립트는 임시 Streamlit 서버를 만들고, 외부 코퍼스·DART·LLM만 고정 데이터로 대체한 뒤
실제 `demo/app.py`를 실행한다. 캡처의 공시 내용은 **UI 검증용 데이터이며 실측 조회 결과가 아니다**.
검사가 끝나면 임시 서버와 파일을 정리한다.

| 검사 | 결과 |
| --- | --- |
| 키보드 Enter로 전원 켜기 | 입력 위젯 등장, LED 켜짐 |
| 최초 전원 켜짐 | `crtOn` 1회 적용 |
| 종목 선택·예시 선택·조회 후 | 켜짐 애니메이션 없음 |
| 결과·오류 화면 | 밝은 출력물, 기존 조회 기준·이전 결과 안내·오류 복구 안내 유지 |
| 로컬 폰트 | 두 폰트 로드, 입력·결과 본문 Pretendard 적용 |
| 모션 축소 | 켜짐 애니메이션 없음 |
| 모바일 400px | 결과 화면 가로 넘침 없음, 컬럼 세로 배치 |
| 사전 | 모바일에서도 검색 입력 접근 가능 |
| 외부 브라우저 요청 / 페이지 오류 | 각각 0건 |

원시 측정: [results.json](terminal-ui/results.json).
최초 리터치 캡처(잡음 보강·결과 페이지 분리 전)는 제3자 배경 이미지가 찍혀 있어 공개 전환 시 제거했다.

## 후속 보강 — CRT 잡음 (2026-09-23)

사용자가 원본 레퍼런스 확인 후 요청한 움직이는 화면 질감을 `retro.css`에 추가했다.
자체 SVG 잡음 타일을 0.64초 주기의 다섯 위치로 이동하고, 3px 주사선 위에 9초마다
간섭 띠가 한 번 내려간다. 글자·위젯에는 변형을 적용하지 않는다.
입력 포커스에서 잡음 불투명도는 0.17 → 0.055, 주사선 레이어는 1 → 0.55로 낮아지고
간섭 띠는 숨겨진다. 꺼진 화면에는 잡음 애니메이션이 없다.

이번 변경은 표시 CSS에 한정해 기존 회귀 검사와 실제 브라우저 확인으로 검증했다.
위 최초 구현의 전체 테스트·커버리지·자동 캡처 결과와 이번 측정은 구분한다.

| 검사 | 이번 결과 |
| --- | --- |
| `.venv/bin/python -m pytest tests/test_demo_app.py -q` | **72 passed**, 23.60초 |
| Chrome, CUA로 전원·한글 입력 조작 | 입력 사용 가능, 포커스 시 잡음 감소 확인 |
| Chrome Animations 패널 | `::before`의 `crtGrain`, `::after`의 `crtTracking` 트랙 확인 |
| `prefers-reduced-motion: reduce` 에뮬레이션 후 새 진입 | Animations 패널에 재생 효과 없음, 정지 질감만 표시 |
| Responsive 400 × 860 | 입력·예시 배치와 화면 경계 확인, 베젤 밖 잡음 없음 |
| 진단 패널 | 폼의 autocomplete·label 경고 표시. 이번 CSS 변경 범위의 폼 속성 수정 없음 |

검증 후 모션 에뮬레이션은 해제했다. 이번 잡음 효과를 Safari·실제 모바일 기기에서 검사하거나
CPU/GPU 부하를 계측하지는 않았다. 레퍼런스의 영상·이미지를 재사용하지 않아 추가 다운로드는 없다.

## 범위와 한계

- 브라우저는 macOS Chromium으로 검사했다. Safari·실제 Windows는 이번에 실행하지 않았다.
- 실제 LLM 응답 품질·콜드 빌드 시간을 다시 측정한 작업은 아니다. 해당 조회 로직은 유지했다.
- `?power=on`은 전원 켜짐 연출만 건너뛴다. 데이터 로딩이나 조회 시간을 없애는 옵션이 아니다.
- 폰트 로컬 제공에는 루트 `.streamlit/config.toml`의 정적 파일 설정이 필요하므로 저장소 루트에서 실행한다.
- 400px 결과 캡처는 고정 사실 카드와 맥락 미표시 안내를 사용한다. 실제 Graphviz 맥락 그래프의 모든 데이터 조합을 시각 검증한 것은 아니다.
