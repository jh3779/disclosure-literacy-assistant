# DART 실측 원문 픽스처

A6(`chunk_disclosure`) 조사·검증 과정에서 실제 `document.xml` API로 받은 공시 원문을
저장해둔 것이다(**업무 담당자 연락처만 마스킹**, 아래 "마스킹과 라이선스" 참고). 손으로
지어낸 합성 데이터(`tests/test_ingest_chunk_disclosure.py`의 `STRUCTURED_XML` 등)와 달리,
DART 원문이 실제로 어떻게 지저분한지(이스케이프 안 된 `&`, 속성 안 큰따옴표, 두 포맷
혼재 등)를 그대로 보존한다.

| 파일 | rcept_no | 회사 | 포맷 | 비고 |
| --- | --- | --- | --- | --- |
| `family_a_capital_increase_20260910000580.xml` | 20260910000580 | 우성머티리얼스 | Family A(구조화 XML) | `<SECTION-1><TITLE>` 정상 존재 |
| `family_a_stake_disclosure_20260910000575.xml` | 20260910000575 | 조일알미늄 | Family A | `<SECTION-1>` 은 있으나 `<TITLE>` 없음 — `section_title` 폴백 케이스 |
| `family_a_treasury_stock_20260910000386.xml` | 20260910000386 | 시프트업 | Family A | 자기주식취득 신탁계약 해지 |
| `family_b_supply_contract_20260911800004.xml` | 20260911800004 | 한화에어로스페이스 | Family B(HTML) | 표 하나짜리 단순 케이스 |
| `family_b_agm_result_20260910800272.xml` | 20260910800272 | 삼성FN리츠 | Family B(HTML) | **한 문서 안에 번호 체계가 두 번 등장**(본문 표 + "세부내역" 표 각각 1부터 재시작) — `chunk_disclosure` 가 표 단위로 분리해야 하는 이유가 된 실제 사례 |
| `family_b_equity_investment_20260910800627.xml` | 20260910800627 | HD현대 | Family B(HTML) | 타법인 주식 취득 |

파일명은 `<rcept_no>.xml` (zip 안 원본 파일명)로 저장돼 있던 것을 사람이 읽기 쉽게
`family_<A|B>_<설명>_<rcept_no>.xml` 로 바꿨고, 아래 마스킹을 제외하면 내용은 API가 준
그대로다(디코딩은 UTF-8, pre-commit 훅이 트레일링 공백·파일 끝 개행만 정리함 — 파싱에
영향 없음).

## 마스킹과 라이선스

- **마스킹**: 공개 전환 때 `RSP_MAN*`(업무 담당자 이름·부서·직위·전화·팩스·이메일) 칸의 값을
  `(마스킹)`으로, `(전  화)` 뒤 연락처 숫자를 `0`으로 바꿨다. 태그·속성·표 구조는 그대로라
  파서 테스트에 영향이 없다. 새 픽스처를 추가할 때도 같은 칸을 먼저 마스킹한다.
- **라이선스 예외**: 이 디렉터리의 XML은 DART에 제출된 공시 원문이며, 저작권과 이용 조건은
  각 제출 회사·DART 정책을 따른다. 저장소의 MIT 라이선스는 **이 디렉터리의 XML
  파일에는 적용되지 않는다**(테스트 코드와 이 README에만 적용).
