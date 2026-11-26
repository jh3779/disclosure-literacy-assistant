# 01. 요구사항 (Requirements)

> 인터뷰 영역 4·7 (범위·MVP / 제약·비기능) 정리 · 상태: confirmed
> 근거: DESIGN.md 확정 파라미터·아키텍처, 실제 코드(`disclosure_lit/*`), GitHub Issue
> #32/DEC-005(pipeline.py 인터페이스 확정), PR #27(임베딩 백엔드 확정)
>
> 아래 기능 표는 최초 기획의 추적용 기록이다. 현재 데모는 종목 선택이 필수이며 사후 이벤트
> 카드 대신 서술형 맥락 지도를 표시한다. 화면 동작은 [03_screen_contract.md](03_screen_contract.md)를 따른다.

## 범위 — MoSCoW

| 구분 | 항목 | 근거 |
| --- | --- | --- |
| **Must** | 입력창 하나로 루머/공시 문장 수신, 종목 클레임 자동 분류 | REQ-F-001 |
| **Must** | 종목 특정 시 DART 공시 대조로 사실만 나열(등급 없음) | REQ-F-002 |
| **Must** | 공시 원문에 상투구 매칭 시 과거 통계 자동 주석 | REQ-F-003 |
| **Must** | 화법 문구만 단독 검색하는 진입점 | REQ-F-004 |
| **Must** | 근거 공시 원문(rcept_no 등) 링크/노출 | REQ-F-005 |
| **Must** | 긍정/부정 사실 동일 비중 노출(색상 비대칭 금지) | REQ-F-006 |
| **Must** | 표본 부족(예: 3건 이하) 클러스터는 "유의하지 않음" 명시 | REQ-F-007 |
| Should | gold set 20~30건 기반 정밀도·재현율 평가 | REQ-F-008 |
| Should | 화법 클러스터 라벨 20개 이상 확보(15개+ 유의 표본) | REQ-F-009 |
| Could | 종목별 공시 히스토리 브라우징 UI | — |
| Could | 다중 클레임 배치 처리 | — |
| **Won't (MVP 제외)** | 매수/매도 추천·목표가·등급·신호등 (안티골, 규제 위반) | 범위 통제 |
| **Won't (MVP 제외)** | 비상장·코넥스 종목 커버리지 | 스코프 통제(코스피·코스닥만) |
| **Won't (MVP 제외)** | 실시간 알림/구독, 계정/로그인, 포트폴리오 연동 | 범위 통제 |

## 기능 요구사항 (REQ-F-*)

| ID | 요구사항 | 우선순위 | 관련 SCR/FLOW |
| --- | --- | --- | --- |
| REQ-F-001 | 사용자는 입력창 하나에 루머든 공시 문장이든 입력할 수 있다 | Must | FLOW-001 · SCR-001 |
| REQ-F-002 | 시스템은 종목이 특정되면 DART 공시와 대조해 사실만 나열한다 | Must | FLOW-001 · SCR-001 |
| REQ-F-003 | 시스템은 팩트체크 결과에 포함된 공시 원문에서 상투구를 자동 매칭해 과거 통계를 주석으로 붙인다 | Must | FLOW-001 · SCR-001 |
| REQ-F-004 | 종목이 특정되지 않으면 입력 문장 자체를 화법 문구 단독 검색으로 처리한다 | Must | FLOW-001 · SCR-001 |
| REQ-F-005 | 팩트체크 항목마다 근거 공시 원문을 펼쳐볼 수 있다 | Must | SCR-001 |
| REQ-F-006 | 뒷받침하는 사실/배치되는 사실은 동일한 시각적 비중(레이아웃·색상)으로 노출한다 | Must | SCR-001 |
| REQ-F-007 | `ClusterStats.is_significant=False`면 경고 문구를 보여주고 수치를 강조하지 않는다 | Must | SCR-001 |
| REQ-F-008 | `eval/run_eval.py`가 gold set 대비 정밀도·재현율을 계산한다 | Should | — |
| REQ-F-009 | `dictionary/cluster.py`가 상투구를 임베딩 유사도로 클러스터링한다 | Should | — |

## 비기능 요구사항 (REQ-NF-*)

| ID | 항목 | 기준 |
| --- | --- | --- |
| REQ-NF-001 | 플랫폼 | Web(Streamlit 데모, `demo/app.py`) |
| REQ-NF-002 | 오프라인 | 불필요 — DART OpenAPI·LLM API 의존, 오프라인 동작 요구 없음 |
| REQ-NF-003 | 프라이버시/규정 | 자본시장법상 유사투자자문업 회피가 핵심 제약(등급·신호등·매수매도 추천 금지) — 개인정보 수집 없음(입력 문장 외 사용자 식별 정보 없음) |
| REQ-NF-004 | 성능/응답성 | 루머 입력 → 결과 카드 출력까지 10초 이내(발표 데모 기준, DESIGN.md 성공 지표) |
| REQ-NF-005 | 접근성 | 데모 최소 기준만: Streamlit 기본 컴포넌트 사용(자체 커스텀 위젯으로 키보드 포커스 깨뜨리지 않음) + 명도 대비는 Streamlit 기본 테마에 위임. 별도 WCAG 등급 목표는 두지 않는다(팀 3인·발표 데모 스코프, 2026-09-11 결정, OQ-006) |

## 제약

- 기술 스택: uv + Python 3.12, Streamlit(>=1.63), 임베딩 `dragonkue/BGE-m3-ko`(로컬,
  PR #27 코멘트에서 A·B·C 전원 합의 완료 — `EMBED_BACKEND=local`, `EMBED_DIM=1024`)
- LLM: provider 스왑(`narrative_qa/llm.py::complete` 재사용 예정, openai/anthropic/gemini)
- 일정/예산: **미확정** — 기획서 마감일·중간발표일·최종발표일 모두 TBD
  (PRD-MVP.md 6절에 명시된 미해결 항목, [06_open_questions.md](06_open_questions.md) OQ-004)
- 되돌릴 수 없는 동작: 없음 — 읽기 전용 조회 서비스(삭제·결제·계정 변경 등 파괴적 동작
  자체가 스코프에 없음)
