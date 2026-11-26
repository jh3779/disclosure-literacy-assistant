# 04. 데이터 모델

> 인터뷰 영역 6 정리 · 상태: confirmed(실제 dataclass 기준, DEC-005로 구조 동결)
> 근거: `disclosure_lit/{dart/ingest.py,factcheck/claim.py,factcheck/verify.py,
> dictionary/stats.py,report/card.py,retrieval/phrase_index.py}`

## 엔티티 (ENT-*)

### ENT-001 · DisclosureSection (`dart/ingest.py`)

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| rcept_no | str | DART 접수번호 |
| corp_code | str | DART 내부 종목코드(8자리) |
| form_type | str | 표준 서식 유형(`normalize_form_type` 결과, 예: "유상증자") |
| section_title | str | 서식 항목/표 제목 |
| text | str | 섹션 본문 |
| rcept_dt | str | 접수일자(기본값 `""`, DEC-005에서 추가 — `CompanyDisclosureIndex.search()` 정렬 기준) |

### ENT-002 · Claim (`factcheck/claim.py`)

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| corp_name | str \| None | 루머 문장에서 추출된 종목명 |
| corp_code | str \| None | DART corp_code — `extract_claim()`이 직접 못 뽑으면 `resolve_corp_code()`가 `corp_name`으로 보강(DEC-005) |
| keyword | str | 공시 검색용 핵심 키워드 |
| raw_text | str | 원본 입력 문장 |

### ENT-003 · FactCheckItem / FactCheckResult (`factcheck/verify.py`)

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| FactCheckItem.fact | str | 사실 문장 |
| FactCheckItem.supporting_section | DisclosureSection \| None | 근거 원문(없을 수 있음) |
| FactCheckItem.polarity | str | "positive" \| "negative" \| "neutral" — 동일 비중 노출용 |
| FactCheckResult.claim | str | 대조 대상 클레임 텍스트 |
| FactCheckResult.items | list[FactCheckItem] | 사실 목록 |

### ENT-004 · ClusterStats (`dictionary/stats.py`)

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| cluster_id | str | 상투구 클러스터 ID |
| sample_count | int | 라벨링된 표본 수 |
| outcome_label | str | 사후 이벤트 라벨(예: "유상증자") |
| outcome_count | int | 해당 이벤트로 이어진 건수 |
| is_significant | bool | 표본 부족(예: 3건 이하) 시 False — REQ-F-007 |

### ENT-005 · PhraseCluster (`retrieval/phrase_index.py`)

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| cluster_id | str | 클러스터 ID |
| representative_phrase | str | 대표 상투구 |
| sample_count | int | 표본 수 |

### ENT-006 · ReportCard (`report/card.py`)

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| fact_check | FactCheckResult \| None | 종목 미특정 시 None |
| phrase_stats | ClusterStats \| None | 매칭 실패 시 None(섹션 생략 정책, DEC-005) |

## 관계 · 소유 단위

```text
CompanyDisclosureIndex (corp_code 기준) 1 ── N DisclosureSection
FactCheckResult 1 ── N FactCheckItem
FactCheckItem 0..1 ── DisclosureSection (supporting_section)
ReportCard 1 ── 0..1 FactCheckResult
ReportCard 1 ── 0..1 ClusterStats
PhraseClusterIndex 1 ── N PhraseCluster
```

- 소유/공유 단위: 없음(멀티테넌시·계정 개념 자체가 스코프 밖 — REQ-F 범위, 01_requirements.md Won't)
- 접근 규칙: 전 사용자 동일 읽기 전용 조회, 쓰기 경로 없음

## 저장 · 동기화

- 저장 위치: **현재 인메모리** — `CompanyDisclosureIndex._by_corp: dict[str, list[DisclosureSection]]`,
  `PhraseClusterIndex`도 동일 패턴 예상(B 구현 전)
- 영속화 방식: **미정** — 이번 세션 시점(2026-09-11)까지 A의 `scripts/collect_disclosures.py`
  (#31)가 아직 병합 전, 실제 대량 코퍼스를 디스크/DB에 영속화할지 여부는
  [06_open_questions.md](06_open_questions.md) OQ-005 참고
- 오프라인 가용성: 해당 없음(REQ-NF-002)
