# 종목 선택 로딩 개선 — 검증 기록

2026-09-23. 사용자 피드백: “종목 선택시 생기는 로딩 화면이 좀 어색함”.
별도 계획 파일 없이 이 피드백과 실제 호출 경로에서 아래 사용자 경로를 도출했다.

- 종목 선택은 보유 공시 안내만 갱신하며 전체 코퍼스나 LLM 조회를 실행하지 않는다.
- 같은 종목에서 문장을 편집해도 공시 파일을 반복해서 읽지 않는다.
- 보유량·기간·주요 유형 안내와 확인 버튼을 통한 실제 조회는 유지한다.

## 원인과 변경

`_company_query_hint()`가 `_local_sections()`를 호출해 첫 선택 시 114개 회사의 공시
파일 전체(1492.1MiB)를 읽었다. “이미 로드된 코퍼스라 영향이 없다”는 기존 주석과 달리,
전원 진입 화면에서는 이 캐시가 비어 있을 수 있었다.

선택한 회사의 `sections.jsonl`만 읽도록 바꾸고, `st.cache_data(show_spinner=False)`로
작은 안내 문자열만 캐시한다. 원문 전체를 회사별 안내 캐시에 보관하지 않는다.
실제 확인 버튼의 조회 스피너·경과 시간과 오류 처리는 그대로 동작한다.

## RED / GREEN

| 단계 | 실제 명령·결과 |
| --- | --- |
| RED | `.venv/bin/python -m pytest tests/test_demo_app.py -k selecting_company_does_not -q` → **1 failed, 72 deselected** |
| GREEN | `.venv/bin/python -m pytest tests/test_demo_app.py -q` → **73 passed**, 23.85초 |
| 전체 회귀·커버리지 | `HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 uv run --with coverage python -m coverage run --data-file=/private/tmp/retro-ui/company-selection.coverage --source=disclosure_lit.demo --branch -m pytest -q` → **545 passed, 12 skipped, 8 deselected**, 38.94초 |
| 커버리지 보고 | `uv run --with coverage python -m coverage report --data-file=/private/tmp/retro-ui/company-selection.coverage -m` → demo **86%**, `terminal.py` **100%** |
| 코드 검사 | 변경 Python 3개 파일 Ruff check / format check 통과 |
| 브라우저 | CUA Chrome에서 경농 첫 선택(211건) → 교보증권 전환(16,632건) 안내 갱신 확인. 전체 코퍼스 로딩 문구 없음 |

RED 체크포인트: `cbf3ae7`. GREEN 체크포인트: `5caa659`.
첫 테스트 실행에서 초기 `.run()`을 누락한 테스트 작성 오류를 고친 뒤, 전체 코퍼스 호출에
의한 실패를 다시 확인해 RED로 기록했다. 초기 테스트 작성 오류는 재현 근거에 포함하지 않는다.

## 테스트가 보장하는 것

| 보장 | 근거 |
| --- | --- |
| 종목 선택 시 전체 코퍼스·실제 조회 호출 없음 | `TestCompanyQueryHint.test_selecting_company_does_not_load_full_corpus_or_run_lookup`: 해당 호출을 예외로 막아 둔 AppTest에서 선택과 안내 표시 성공 |
| 선택한 회사 파일만 읽고 문장 편집 시 재사용 | 같은 테스트에서 경로와 1회 읽기 검증 |
| 빈 코퍼스·날짜 누락·다른 회사 제외·유형 집계 유지 | 기존 `TestCompanyQueryHint` 단위 테스트 |
| 조회 당시 종목 전달·직전 결과 보존 유지 | 기존 `tests/test_demo_app.py` 회귀 검사 |

## 로컬 측정과 한계

동일 머신에서 Python 함수 시간을 측정했다. OS 파일 캐시는 비우지 않았으므로 디스크의
완전한 콜드 스타트나 클릭부터 화면 표시까지의 전체 지연을 뜻하지 않는다.

| 대상 | 첫 읽기·안내 | 안내 캐시 재사용 |
| --- | --- | --- |
| 기존 전체 파일 읽기, 82,876개 섹션 | 2.641초 | 해당 없음 |
| 경농, 회사 파일 3.81MiB | 7.5ms | 0.05ms |
| 최대 파일인 교보증권, 314.77MiB | 474.8ms | 0.07ms |

큰 회사의 첫 파일 읽기는 여전히 비용이 있다. 안내 캐시는 앱 실행 중 유지되며 수집 데이터를
교체하면 Streamlit 캐시를 비우거나 앱을 재시작해야 한다. 실제 LLM 조회 지연은 이번 범위가 아니다.
