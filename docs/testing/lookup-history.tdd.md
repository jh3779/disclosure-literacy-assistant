# 조회 기록과 서류 분할 화면 — 검증 기록

2026-09-24. 사용자 요청에서 도출한 경로: 봤던 결과를 메모리에 보관하고 사전 옆에서
서류를 골라 읽는다. `design-review` layout·adapt 지침과 독립적인 소스 기반 배치 리뷰를
적용했다. 기존 원본 방 배경과 출력물 디자인을 유지한다.

## 구현과 보관 범위

- 사전 옆 세 번째 **조회 기록** 탭. 데스크톱에서 왼쪽 목록·오른쪽 결과, 900px 이하에서
  위아래 배치한다. 1100px 이하의 사실 그룹도 세로로 배치한다.
- 성공한 조회의 문장·종목·카드·샘플 표시와 맥락 DOT/미표시 사유·코퍼스 범위를 보관한다.
  기록 열람은 카드 생성이나 임베딩 매칭을 다시 호출하지 않고 프린터 연출도 반복하지 않는다.
- 최신 30건을 앱 프로세스 메모리에 깊은 복사한다. 잠금으로 세션 간 읽기·쓰기를 보호한다.
  같은 서버의 새 세션에서 열 수 있고 서버 재시작·캐시 삭제로 초기화된다. 계정별 저장은 아니다.
- 오류는 기록에 넣지 않고 이전 기록을 유지한다. 정상 빈 결과·샘플은 표시 상태 그대로 저장한다.
- 새 버전에서 조회한 결과부터 보관한다. 이전 버전/별도 서버의 세션 결과를 소급 수집하지 않는다.

## TDD 근거

RED `6f6b039`: 신규 5건이 탭·저장소 부재로 실패했다.
GREEN `1e7e23f`: 같은 5건이 통과했다. AppTest 1.63은 keyed tabs 활성 상태를 다음 실행에
직렬화하지 않아 테스트에서 매 실행 전에 공개 세션 키로 활성 탭을 지정했다. 실제 탭 클릭과
라디오 선택은 CUA 브라우저에서 별도로 검증했다.

| 보장 | 테스트 | 결과 |
| --- | --- | --- |
| 사전 옆 기록 탭과 빈 상태 안내 | `TestLookupHistory.test_history_tab_follows_glossary_and_explains_empty_state` | PASS |
| 새 세션에서 예전 문서 선택, 재조회·재매칭·출력 연출 없음 | `test_new_session_can_choose_older_document_without_requery` | PASS |
| 입력 초안 유지, 실패 후에도 이전 문서 보존 | `test_history_selection_does_not_overwrite_draft_and_errors_do_not_erase_history` | PASS |
| 최신순·보관 상한·동일 조회의 고유 ID | `test_history_keeps_latest_entries_and_gives_repeat_queries_distinct_ids` | PASS |
| 원본/읽은 카드 변경이 보관 데이터에 전파되지 않음 | `test_history_snapshots_are_independent_of_source_and_read_mutations` | PASS |
| 저장된 맥락 그래프·범위 표시, 만료된 선택은 최신 서류로 복구 | `test_saved_graph_scope_and_evicted_selection_survive_new_session` | PASS |

```bash
.venv/bin/python -m pytest tests/test_demo_app.py tests/test_demo_history.py -q \
  -k 'TestLookupHistory or test_history_'
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 uv run --with coverage python -m coverage run \
  --data-file=/private/tmp/history-evidence/coverage \
  --source=disclosure_lit.demo --branch -m pytest -q
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 uv run --with coverage python -m coverage run \
  --append --data-file=/private/tmp/history-evidence/coverage \
  --source=disclosure_lit.demo --branch -m pytest tests/test_demo_app.py tests/test_demo_history.py -q
uv run --with coverage python -m coverage report \
  --data-file=/private/tmp/history-evidence/coverage -m
```

- RED: **5 failed, 84 deselected**, 2.27초. GREEN: **5 passed, 84 deselected**, 2.33초.
- 전체: **561 passed, 12 skipped, 8 deselected**, 60.73초.
- 이후 맥락 그래프·선택 만료 테스트 1건 추가: 관련 **6 passed**, UI+저장소 **90 passed**, 50.08초.
  그 이후 제품 변경은 목록·컬럼의 반응형 CSS에 한정했다.
- 분기 포함 커버리지: demo **89%**, `app.py` **88%**, `history.py` **96%**, `terminal.py` **100%**.
- 스킵/제외된 기존 통합 테스트는 이번에 실행하지 않았다. 실제 LLM 품질·콜드 빌드 성능도 재측정하지 않았다.

## 브라우저 확인

CUA Chrome, 외부 호출을 고정한 `tests/fixtures/terminal_preview.py` 기반 임시 서버에서 검증했다.

- 빈 상태 → 입력 이동 → 서로 다른 문장 두 건 조회 → 결과 페이지의 조회 기록 버튼 →
  최신·이전 서류 선택 → 새로고침 후 두 건 유지.
- 저장 문서의 프린터 animation은 `none`. 방향키로 서류를 선택하면 선택 표시와 본문이 변경된다.
- 데스크톱 1510px: 전체 작업 영역 1200px, 목록 약 295px, 출력물 약 809px.
- 긴 문장·30건 목록을 별도 고정 데이터로 확인했다. 목록의 `flex-wrap`을 해제해 한 열의 세로
  스크롤을 유지한다. 중간 폭의 사실 그룹이 옆으로 밀리지 않도록 flex 줄바꿈도 해제했다.
- 최종 800px: 목록·문서 모두 768px 너비로 위아래 배치. 320px: 문서 너비 320px,
  목록·출력물 좌우 경계 13.6~306.4px. 1000px에서도 사실 그룹은 세로로 배치되고 화면 안에 들어온다.
- 작은 화면 목록 높이는 240px, 30건 내용 높이는 3032~5051px로 목록 안에서 스크롤한다.
- 임시 서버 재시작 중 연결 종료 로그가 발생했다. 본 앱 8509의 신규 탭은 경고·오류 0건이다.
  임시 viewport는 복원하고 검증 탭·서버는 정리한다.

Impeccable 자동 detector/overlay 도구는 미설치로 사용하지 않았다. 실제 스크린리더, Safari,
실기기 모바일·OS 모션 설정은 직접 검증하지 않았다. 새 CSS는 기록 영역에 제한한다.

미리보기: <http://127.0.0.1:8501/?power=on>.

2026-09-24 11:18 후속 운영 확인: 사용자가 잔존 포트 정리와 서버 하나만 유지를 요청했다.
기존 8501/8502/8504/8506/8507/8508/8509의 프로젝트 서버 7개를 종료하고 최신 앱 하나를
8501에서 실행했다. 기존 브라우저는 구버전 8508/result를 보고 있었으며 8509는 HTTP 200으로
응답했다. 사용 중인 탭을 새 주소로 이동해 입력·조회 기록·입력 복귀 화면을 직접 확인했다.
`lsof`와 프로세스 목록에서 Streamlit 하나, health·배경 자산 HTTP 200 확인. 재시작으로
메모리 기록은 초기화된다. 프로젝트 외 서버·시스템 서비스는 종료하지 않았다.
