# 06. 미해결 질문 · 가정 · 결정 로그

> 인터뷰 영역 8 + 진행 중 파생 · 상태: living document
> 확정된 아키텍처 결정의 정본은 [.agent/DECISIONS.md](../.agent/DECISIONS.md)(DEC-001~007) —
> 이 문서는 그걸 중복 기록하지 않고 ID로만 참조한다. 최종 갱신: 2026-09-11.

## 해결됨 (Resolved, 2026-09-11 감사)

<!-- markdownlint-disable MD060 -->
| ID | 질문 | 해결 내용 |
|---|---|---|
| OQ-002 | `demo/app.py`의 `run_factcheck` 호출이 `corp_code_index` 인자 누락 | 감사 시점에 이미 작업 트리에서 수정 진행 중이었음(`report/card.py::build_report`가 `text, company_index, corp_code_index, phrase_index`를 받도록 정리, `app.py`의 중복 `_build_card` 로직 제거) — `uv run pytest tests/test_report_card.py tests/test_pipeline.py` 13 passed로 확인 |
| OQ-006 | 접근성 요구 수준 | "데모 최소 기준만"으로 결정 — 01_requirements.md REQ-NF-005 갱신(Streamlit 기본 컴포넌트·기본 테마 대비에 위임, 별도 WCAG 목표 없음) |
| OQ-007(신규) | CLAUDE.md 팀 분담 표·`dictionary/{cluster,stats}.py`·`retrieval/phrase_index.py` 독스트링이 L2b 담당을 B로 잘못 적고 있었음(원안은 C) | DEC-007로 기록, 관련 파일 전부 C로 정정. 김가빈(9abin)에게 Issue #37로 통지, 회신 대기 |
<!-- markdownlint-enable MD060 -->

## 진행 중 (구현은 다른 PR에서 진행)

<!-- markdownlint-disable MD060 -->
| ID | 질문 | 현재 상태 |
|---|---|---|
| OQ-003 | 임베딩 백엔드(`local`+`dragonkue/BGE-m3-ko`+`dim=1024`) 구두 합의가 코드에 미반영 | 결정 자체는 DEC-006으로 확정. **구현은 PR #35**(powercodingman, `feat/company-index-embedding`, 2026-09-11 기준 open)에서 `local_embed_model`/`embed_model`(openai 스왑용) 분리까지 포함해 더 완성도 높게 진행 중 — narrative_qa `config.py`가 이미 `EMBED_MODEL`을 openai 백엔드용으로 쓰고 있어서 키가 겹치면 값이 꼬이는 문제까지 해결함. 저(C)도 별도로 `.env.example`/`config.py`를 직접 고쳐 PR #36을 냈다가, PR #35가 이 문제까지 해결한 상위 호환임을 확인하고 PR #36은 닫음(중복 작업 회수) — 이 문서 작업 중 로컬에 남아있던 중복 수정본도 되돌림. PR #35 머지되면 이 항목 해결로 이동 |
<!-- markdownlint-enable MD060 -->

## Open Questions (미결정)

<!-- markdownlint-disable MD060 -->
| ID | 질문 | 영향 | 담당 | 필요 시점 |
|---|---|---|---|---|
| OQ-001 | `scripts/collect_disclosures.py`(#31, `origin/feat/collect-disclosures` 브랜치)가 `chunk_disclosure()` 호출 시 `rcept_dt=meta.rcept_dt`를 실제로 채워 넣었는가 — 2026-09-11 재확인 결과 해당 브랜치 코드(`chunk_disclosure(raw, rcept_no=meta.rcept_no, corp_code=meta.corp_code)`)에 여전히 `rcept_dt` 인자 없음, 미해결 유지. 기본값(`""`)이라 지금 당장 깨지진 않지만 방치하면 최신순 정렬이 무의미해짐 | REQ-F-002 정렬 품질 | A(powercodingman) | #31 리베이스 시점 |
| OQ-004 | 정확한 일정(기획서 마감일·중간발표일·최종발표일) | 전체 마일스톤 페이싱 | 전원(강의 측 공지 대기) | 2026-09-11 확인 — 강의 측에서 아직 미공지. 공지되는 대로 이 문서와 PRD-MVP.md 6절에 반영 |
| OQ-005 | 대량 코퍼스 영속화 방식(현재 인메모리 dict) — Phase 1 종료 후 실제 저장소가 필요한지, 필요하면 무엇을 쓸지 | 04_data_model.md 저장 전략 | A·C | Phase 1 Exit 이후 (2026-09-11 기준 Phase 1 아직 진행 중 — 재론 시점 아님) |
<!-- markdownlint-enable MD060 -->

## 가정 (Assumptions)

잠정으로 두고 진행한 전제. 틀리면 재검토 대상.

<!-- markdownlint-disable MD060 -->
| ID | 가정 | 근거/리스크 |
|---|---|---|
| ASM-001 | 로컬 임베딩(`dragonkue/BGE-m3-ko`)이 속도·자원 면에서 병목이 아니다 | 근거: Issue #29(Apple M5 Max/MPS 실측)·#30(RTX 4070 Laptop 실측) 두 환경 모두 확인, CPU 최악 케이스도 풀스케일 인덱싱이 시간 단위 안에 끝남. 리스크: 실제 코퍼스 규모가 이 테스트(수백 건)보다 훨씬 크면 재검증 필요 |
| ASM-002 | 코스피·코스닥 전체가 아니라 루머 사례 중심의 제한된 종목 스코프로도 MVP 성공 지표(gold set 20~30건, 클러스터 15개+) 달성 가능 | 근거: 실제 코퍼스 목표 건수를 A가 아직 공식 확정하지 않음(OQ-005와 연결). 리스크: 스코프가 예상보다 커지면 ASM-001도 같이 재검증 필요 |
| ASM-003 | Streamlit 단일 페이지 모델로 REQ-F-001~007을 전부 충족할 수 있다(별도 라우팅 불필요) | 근거: 실제 `demo/app.py` 구현(PR #33)이 이 가정으로 작동 확인됨. 리스크: 낮음 |
<!-- markdownlint-enable MD060 -->

## 결정 로그 (DEC-*)

확정된 아키텍처/범위 결정은 [.agent/DECISIONS.md](../.agent/DECISIONS.md)가 정본이다.
이 문서에서는 discovery-interview 문서 세트에 직접 영향을 준 항목만 ID로 인용한다.

<!-- markdownlint-disable MD060 -->
| ID | 결정 요지 | 이 문서 세트에서의 영향 |
|---|---|---|
| DEC-001 | 기준문서(PRD-MVP.md/DESIGN.md/proposals/*) 잠금 정책 | 00_project_brief.md가 이 문서들을 정본으로 인용만 하고 내용을 복제/수정하지 않음 |
| DEC-002 | config 값 fail-fast는 소비 지점에서 | 04_data_model.md 저장 전략에 직접 영향 없음(참고용) |
| DEC-003 | PR 병합은 항상 작성자 본인 | 01_requirements.md 제약에 반영 |
| DEC-004 | `corpCode.xml`만으로는 코스피/코스닥/코넥스 구분 불가 — `corp_cls` 개별 조회 | 01_requirements.md Won't(코넥스 제외)의 실제 구현 난이도에 영향 |
| DEC-005 | L2a/L2b 파이프라인 인터페이스 확정(Issue #32) — `rcept_dt` 필드 추가, 화법통계 매칭 실패 시 섹션 생략, dataclass 구조 동결, `resolve_corp_code` 헬퍼 분리 | 02_ui_flow.md·03_screen_contract.md·04_data_model.md·05_state_machine.md 전부 이 결정을 반영해 작성됨 |
| DEC-006 | 임베딩 백엔드 확정(local+`dragonkue/BGE-m3-ko`+dim=1024) | OQ-003 결정 근거. 실제 코드 구현은 PR #35 |
| DEC-007 | 파트 분담 정정 — L2b(화법통계 엔진)는 B가 아니라 원안(proposal 11절)대로 C 담당 | CLAUDE.md 팀 분담 표·관련 모듈 독스트링 정정, OQ-007로 추적 |
| DEC-009 | L2b 최초 구현 — 탐욕적 클러스터링(`CLUSTER_MIN_SCORE=0.75`) + 라벨 레지스트리(`register_outcome_labels`), 둘 다 실측 미검증 임시값 | 04_data_model.md ENT-004/ENT-005 스키마 그대로 구현, 05_state_machine.md STATE-002 불변식 충족 확인 |
<!-- markdownlint-enable MD060 -->

## 리스크

<!-- markdownlint-disable MD060 -->
| 리스크 | 심각도 | 완화책 |
|---|---|---|
| 팩트체크 엔진(`verify_claim`)이 groundedness 없이 사실을 만들어낼 경우 — 규제 위반과 직결 | 높음 | narrative_qa의 groundedness guard 패턴 재사용 예정(DESIGN.md), gold set 20~30건으로 정밀도·재현율 80%+ 검증(REQ-F-008) |
| 실제 코퍼스 규모가 ASM-001·ASM-002 가정보다 훨씬 커서 로컬 임베딩 처리 시간이 급증 | 중 | A에게 실제 수집 스코프(종목 수·건수) 확인 요청 완료(Issue #29 코멘트) — 회신 대기 |
| 표본 부족 클러스터의 통계 왜곡(우연히 3~4건만 라벨링된 클러스터를 유의한 것처럼 노출) | 중 | `is_significant` 필드로 최소 표본 미달 시 강제 경고(REQ-F-007, STATE-002 INV-03로 코드 레벨 반영됨) |
| PR #33(`demo/app.py`)이 pipeline.py의 `corp_code_index` 시그니처 변경(DEC-005)을 아직 반영 못 해 머지 후 깨질 수 있음 | 중 | OQ-002로 추적, pipeline.py PR 머지 직후 즉시 반영 필요 |
<!-- markdownlint-enable MD060 -->
