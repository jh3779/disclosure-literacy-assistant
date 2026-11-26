"""공시 섹션 → 화법 클러스터링용 문구 후보 추출 — L2b. 담당: C(우지훈).

`cluster_phrases()`(cluster.py)에 넣을 재료를 만드는 자리. "상투구"를 정확히
정의하는 라벨링 작업(DESIGN.md "팀의 핵심 구현 작업")은 아직 안 끝났다 — 그때까지는
섹션 본문을 문장 단위로 쪼갠 것 전부를 후보로 취급하는 보수적인 근사치다. 실제
라벨링이 시작되면 이 추출 규칙 자체도 재검토 대상.

**2026-09-15 보완**: 실측(실제 코퍼스 1,500건 샘플 클러스터링)으로 마침표 분리만으로는
소유보고서·재무제표 같은 표 양식 텍스트(`dart/ingest.py::_TableSectionParser`가
만드는 섹션)가 대량으로 "문장 후보"에 섞여 들어간다는 게 확인됐다 — 1,314개 클러스터
중 1,250개(95%)가 단일 표본이었고, 표본 수 상위 클러스터가 전부 표 헤더였다(Issue #52
후속 코멘트 참고). `_is_narrative_sentence()`로 서술형 종결어미·숫자 비중 두 신호를
걸러 표 텍스트를 배제한다 — 형태소 분석 없는 정규식 휴리스틱이라 완벽하지 않고,
A의 실제 라벨링 착수 시 재검증 필요.

(참고: 이 커밋은 원래 실험 브랜치(`test/l2b-outcome-labeling-experiment`)에서
"main 병합 대상 아님"이라는 메시지로 만들어졌다가, 실현가능성 검증 결과 실제로
유효한 개선으로 판단해 PR #53에 편입했다 — cherry-pick 시 커밋 메시지 자체는
못 고쳤으니 git log만 보면 혼란스러울 수 있다는 점을 PR #53 리뷰(@9abin)에서
지적받아 여기 명시해둔다. 코드는 의도적으로 포함된 것이 맞다.)

**2026-09-15 추가 보완(PR #57)**: A가 100개사 실 코퍼스로 스모크 테스트하다 위
`_is_narrative_sentence()` 필터만으로는 부족함을 발견 — 필터 없이 고유 후보가
525,843개나 나와 `cluster_phrases()`가 전부 임베딩하기엔 비현실적이었다.
`min_companies`(회사 빈도) 필터를 추가로 얹었다 — 서술형 문장 필터와 회사 빈도
필터는 서로 다른 문제(표 텍스트 오염 vs 스케일)를 풀므로 함께 적용한다.

**2026-09-15 세 번째 보완(PR #57, A)**: 배치 매칭(`PhraseClusterIndex.match_many()`)을
실 코퍼스로 돌리다 CUDA OOM으로 재현 — `_SENTENCE_BOUNDARY`가 마침표 기준이라 마침표가
거의 없는 텍스트(구조를 못 찾은 "본문(미분할)" 폴백 섹션 전체 등)가 통째로 "문장 하나"가
되면서 실제로 32,450자짜리 후보가 나왔다. 이런 극단적으로 긴 항목이 배치에 섞이면 그
배치 전체가 그 길이에 맞춰 패딩되며 어텐션 메모리가 터진다(8GB GPU에서 16GB 할당 시도
확인). `MAX_PHRASE_LENGTH` 상한을 추가했다.

**2026-09-18 네 번째 보완**: 실측(사용자 제보 — 삼성전자 공정거래법 제26조
대규모내부거래 공시)으로 표 양식 공시가 마침표 기준 분리 때문에 거의 전부 유실된다는
걸 확인했다. 두 가지 문제가 같이 있었다: (1) "가.", "나.", "1." 같은 항목 번호
라벨의 마침표가 진짜 문장 경계로 오인되어, 서로 다른 두 라벨 사이 내용이 하나의
가짜 "문장"으로 잘못 이어붙었다(이 실측 케이스의 유일한 후보가 "출자목적물
보통주식 다."였다 — "나." 라벨 뒤와 "다." 라벨 앞 사이 내용이 우연히 서술형
종결어미 검사를 통과한 것). (2) 반대로 실제 서술형 메모(예: "5. 기타" 섹션의
"~함"/"~임"류 문장)는 마침표 없이 "- " 불릿으로만 구분되는 경우가 흔해서, 문장
경계를 못 찾고 통째로 뭉친 뒤 필터를 통과하지 못했다. `_LIST_LABEL`로 항목 라벨을
분리 전에 제거하고, "- " 불릿도 문장 경계로 추가 인정했다 — `_is_narrative_sentence()`
(종결어미·숫자비중 필터)는 표 텍스트 오염을 막던 핵심 안전장치라 그대로 둔다.
`CANDIDATE_EXTRACTION_VERSION`을 추가해 `cache_store.phrase_fingerprint()`에
포함시켰다 — 그 전엔 이런 추출 로직 변경이 지문에 안 잡혀서 디스크 캐시가 조용히
재사용될 뻔했다(회사 빈도/클러스터 문턱만 지문에 있었음).

**2026-09-21 다섯 번째 보완(후보 풀 재설계)**: 골드셋(`eval/gold/rumor_cases.jsonl`)
25건의 실제 근거 공시 섹션 33개(전부 조회공시·해명공시)를 클러스터에 매칭해보니 **0개
적중**이었다. 이 섹션들에는 PRD가 겨냥한 상투구("현재까지 구체적으로 확정된 사항은
없습니다", "다양한 방안을 검토 중이나")가 있는데, 회사마다 고유 내용("석유화학 사업의
경쟁력 강화와…")과 한 문장에 쉼표로 붙어 있어 **문자열이 정확히 같지 않고**, 그래서
"5개사 이상 정확 일치" 필터(`MIN_COMPANIES_DEFAULT`)에서 전부 탈락했다(골드 근거 문장
83개 중 76개가 문자열 기준 단 1개사). 반대로 K-IFRS 주석은 수백 개사에 토씨 하나 안
틀리고 반복되니 풀의 43.8%를 차지했다 — 결과적으로 맥락 지도는 회계주석에서만 켜지고
루머 대조 화면에서는 안 켜졌다. 쉼표 뒤 종결 절만 따로 뽑아봐도 83→121개 중 110개가
여전히 1개사였다(표현이 회사마다 달라 어떤 문자열 필터로도 못 살린다; 임베딩만 묶는다).

**2026-09-21 여섯 번째 보완(서식 경계 + 회계주석 필터)**: 다섯 번째 보완 직후 실측으로
두 가지가 더 드러났다.

(1) 조회공시 답변·해명공시 서식은 항목을 마침표 없이 **줄바꿈**으로만 끊는다("1. 제목:
…답변\\n2. 내용: 당사는 …검토하고 있으나, 현재까지 확정된 사항은 없습니다."). 마침표·불릿만
경계로 보던 탓에 PRD가 겨냥한 바로 그 상투구가 기사 헤드라인과 한 조각으로 붙었다 —
목표 섹션 199개에서 "검토…확정된 사항" 문장 48개 중 9개(19%)가 이렇게 오염됐고, 개행을
경계로 인정하고 서식 항목 라벨("제목:"/"내용:"/"풍문 또는 보도의 내용:")을 떼자 48개가
전부 깨끗해졌다(`_strip_field_label_prefix()`, R104-2 수정 이후 이름 — 상세 판별
규칙은 해당 함수 옆 주석 참고).

(2) A11(`scripts/extract_cliche_candidates.py`)이 쓰던 회계주석 필터가 프로덕션 경로엔
연결돼 있지 않았다 — 다섯 번째 보완 이후에도 풀 상위(회사 수 기준) 문구가 전부 회계기준
주석이었고, centroid 코사인 0.7 이상으로 근접한 클러스터 1,250쌍의 대부분이 이 주석들의
과분할이었다(가장 가까운 쌍 0.906: "해당 기준서의 개정이 재무제표에 미치는 중요한 영향은
없습니다." ↔ "이 개정사항이 연결회사의 재무제표에 미치는 중요한 영향은 없습니다."). A11이
쓰던 두 장치를 모두 가져온다 — 어휘 기반 `_is_accounting_note()`와, 어휘 목록으로는 못
잡는 것까지 일반화해서 거르는 회사 수 비율 상한(`MAX_COMPANY_FRACTION`). 어휘만으로는
부족하다는 건 실측으로 확인했다: 어휘 필터만 걸었을 때 풀 상위 문구가 여전히 "추정 및
가정은 지속적으로 평가되며…", "주요 개정내용은 다음과 같습니다." 같은(회계 어휘가 아예
없는) 주석이었다.

그래서 "회사 빈도" 필터를 두 단계로 나눴다: (1) 클러스터링 *전* 사전 필터는 규모를
묶는 용도로만 느슨하게(`LEXICAL_MIN_COMPANIES`=2, 고유 문장 30만→약 1만) 걸고, 조회공시·
풍문·해명 섹션(`TARGET_SECTION_PATTERN`, 실측 199섹션/33개사)의 서술 문장은 사전 필터
없이 전부 풀에 넣는다(`select_clustering_pool()`). (2) "여러 회사가 같은 말을 쓴다"는
원래 의도는 클러스터링 *후* 클러스터 멤버의 회사 합집합으로 센다 — 문자열이 아니라
의미가 같은 문구들의 회사 수다. **같은 날 후속 변경**: 이 (2)를 처음엔 `PhraseClusterIndex.
build(min_companies=...)`로 구현해 문턱 미달 클러스터를 통째로 버렸는데, 그러면
"매칭 성공 여부"와 "여러 회사 통계로 보여줄 신뢰도"가 하나로 묶여 표본이 적은 진짜
표현까지 `match()`가 아예 못 찾게 됐다(팀 리뷰 지적, 골드셋 잔여 미매칭 6건이
정확히 이 케이스였음). 그래서 `build()`에서는 빼고 `dictionary/cluster_meaning.py::
MIN_COMPANIES_FOR_MEANING`(신뢰도 판정 전용)으로 옮겼다 — `companies_by_phrase`는
그 필터링에 더는 안 쓰지만, `select_clustering_pool()`의 사전 필터 (1)에는 여전히
필요해 반환값으로 남아있다. 불릿 잔존 버그도 같이 고쳤다: 마침표 뒤에 오는 "- " 불릿은
앞선 마침표 경계가 공백을 먼저 소비해 대시가 문장에 남았다("- 다만, …") — 분리 후
선행 "- "를 떼어낸다(`_BULLET_PREFIX`).
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator
from pathlib import Path

from disclosure_lit.dart.ingest import DisclosureSection

_logger = logging.getLogger(__name__)

# 목차/항목 번호 라벨("가.", "나.", "1.", "①" 등)은 문장이 아니라 번호일 뿐인데,
# 마침표로 끝나서 마침표 기준 분리가 이걸 문장 경계로 오인한다(실측 — 위 모듈
# docstring "네 번째 보완" 참고). 분리 전에 라벨 자체를 통째로 제거한다(구분자로
# 남기지 않고 삭제) — 라벨 앞뒤 내용이 라벨을 거쳐 잘못 이어붙는 걸 막는다. 단일
# 한글 음절/숫자 1~2자리/원문자만 대상으로 좁혀서, 두 글자 이상인 실제 단어("짧음."
# 등)는 건드리지 않는다.
_LIST_LABEL = re.compile(r"(?:^|(?<=\s))(?:[가-힣]|[0-9]{1,2}|[①-⑳])\.\s+")

# 2026-09-22(상세 감사, 실측 재현) — K-IFRS 재무제표 주석은 "2-6", "9-2" 같은
# "숫자-숫자" 하위번호 소제목을 앞 문장의 마침표 바로 뒤에 공백 없이 붙인다(위
# `_LIST_LABEL`과 달리 라벨 앞에 공백이 없다). 처리 안 하면 서로 무관한 두 문장이
# 한 후보로 이어붙는다(114개사 코퍼스 실측 77개 섹션에서 재현: "...대가의 선지급이나
# 선수취로 인한 거래일을 각각 결정합니다.2-6 현금및현금성자산재무상태표상의
# 현금및현금성자산은..."처럼 완전히 다른 두 문장이 하나의 클러스터링 후보가 됨).
# 라벨 뒤 공백은 있을 때(553건)도 없을 때(60건)도 있어 `\s*`로 둘 다 받는다 —
# `_LIST_LABEL`과 같은 방식(라벨을 통째로 지우되 공백 하나로 치환)으로 처리하면,
# 원문에 공백이 있었든 없었든 항상 "마침표+공백" 형태가 남아 `_SENTENCE_BOUNDARY`가
# 정상적으로 문장 경계로 잡아낸다.
#
# **2026-09-22 팀 리뷰(@9abin) 지적·수정**: 처음엔 라벨 앞 조건을 `_LIST_LABEL`과
# 같은 `(?:^|(?<=\s))`(마침표 뒤 *또는* 아무 공백 뒤 *또는* 문두)로 잡았는데, "아무
# 공백"이 문장 중간의 평범한 공백까지 다 잡아버려 "당사는 향후 2-3분기 내 실적을..."
# 같은 일반 문장의 "2-3"까지 지워버리는 사고가 났다(실측 재현: `TestAccountingSubnoteLabel`
# 참고). 실제로 개행 뒤(140건)와 마침표 직후(553건)만 진짜 라벨이고, "공백 뒤"로
# 넓게 잡았을 때 걸리는 10,519건은 대부분 도로명주소 번지수("15-10", "12-2" 등)였다
# (전수조사로 확인). 그래서 조건을 `(?<=\.)|^|(?<=\n)`(마침표 직후·문두·**개행**
# 직후만)으로 좁혔다 — "2-2 연결기준연결재무제표는..." 같이 개행 뒤에 오는 라벨은
# 여전히 잡히고, "2-3분기"처럼 문장 중간 공백 뒤에 오는 일반 숫자 범위는 더는
# 안 건드린다. "제4-1조"처럼 문자 바로 뒤에 숫자-숫자가 붙는 법률 조항 번호는
# 애초에 이 세 조건 어디에도 안 걸려 여전히 보존된다.
#
# **2026-09-22 팀 리뷰(@9abin) 재검토 지적·재수정**: `(?<=\.)`가 "마침표"와
# "소수점"을 구분 못했다 — "성장률은 3.5-4.0%로 전망됩니다."에서 "3." 뒤의
# "5-4"가 소수점 뒤 구간 표기인데도 라벨로 오인돼 지워졌다(직접 재현 확인,
# `'3. .0%'`로 손상). 실측으로 확인한 진짜 라벨 553건은 전부 마침표 앞이 숫자가
# 아니었다(0건 예외) — 진짜 문장 종결 마침표는 한글/닫는 인용부호 등 뒤에 오지
# 숫자 뒤에 오지 않는다. 그래서 마침표 조건에 "그 마침표 앞이 숫자가 아니어야
# 한다"는 부정 룩비하인드를 추가했다(`[^0-9]\.` — 고정 폭 2자라 룩비하인드로
# 표현 가능). 문두(`^`)에 마침표가 오는 경우는 없으니 이 조건 변경의 영향이
# 없고, 개행 조건도 그대로 유지된다.
_ACCOUNTING_SUBNOTE_LABEL = re.compile(r"(?:(?<=[^0-9]\.)|^|(?<=\n))[0-9]{1,2}-[0-9]{1,2}\s*")

# 마침표 뒤 공백을 문장 경계로 본다. 국문 공시 본문이 "~다.", "~함." 같은 formal
# 종결어미를 쓰지만 결국 전부 마침표로 끝나므로 이 규칙 하나로 충분하다 — 종결어미
# 자체를 파싱하려 하지 않는다(형태소 분석기 없이 정규식만으로는 오탐이 더 클 것).
# "- " 불릿도 추가 경계로 인정한다 — 실측(위 모듈 docstring): 서술형 메모가 마침표
# 없이 "- "로만 항목이 구분되는 표 양식 공시가 흔했다. 불릿이 섹션 맨 앞(문두)에
# 오는 경우도 있어 `^`도 함께 허용한다 — "-뒤에 공백" 요건은 유지해서
# "-120억원"처럼 부호로 붙은 숫자나 "SPC-법인"류 하이픈 복합어는 건드리지 않는다.
# 개행도 경계다(모듈 docstring "여섯 번째 보완") — 조회공시 답변 서식은 항목을
# 마침표 없이 줄바꿈으로만 끊는다.
_SENTENCE_BOUNDARY = re.compile(r"(?<=\.)\s+|(?:^|\s)-\s+|\n+")

# 공시 서식의 항목 라벨("제목:", "내용:", "풍문 또는 보도의 내용:")은 문장이 아니라
# 서식 필드 이름이다(모듈 docstring "여섯 번째 보완"). 개행 경계를 인정하면 조각이
# 이 라벨로 시작하므로 떼어낸다.
#
# R104-2 (2026-09-21 외부 리뷰) 수정: 예전엔 "한글·공백 12자 이내 + 콜론"이면 전부
# 라벨로 간주했다. 그런데 이 패턴은 "다음은 사실이 아닙니다:"처럼 부정·조건문의
# 앞부분과 실제 서식 라벨을 구분하지 못해, 원문의 부정 문맥을 통째로 삭제해 후보
# 문장의 의미를 반대로 뒤집는 사고가 났다(실측 재현: `TestFormFieldBoundary` 참고).
#
# 처음엔 실제 관측된 라벨만 화이트리스트로 좁히는 방법을 썼는데, 독립 재검토
# 과정에서 그 조사 범위가 TARGET_SECTION_PATTERN 대상 섹션에 한정돼 있었다는 게
# 드러났다 — `_FIELD_LABEL_PREFIX`는 섹션 종류를 안 가리고 전부에 적용되는데
# (`_match_cluster_for_text()`의 임의 근거 섹션 포함), 실제로 전체 코퍼스를
# 다시 조사하니 목표 섹션 밖에도 "장소:", "정정사유:", "종료일:" 등 고유 라벨
# 후보가 463개(빈도 5회 이상만 315개)나 있어 화이트리스트로는 감당이 안 됐다.
#
# 그래서 "알려진 라벨 목록"이 아니라 "라벨의 문법적 성질"로 판별한다 — 한국어
# 서식 라벨은 전부 명사구라 종결어미 "-다"로 끝나지 않는다(실측: 위 463개 고유
# 라벨 후보 중 "-다"로 끝나는 건 0개). 반대로 "다음은 사실이 아닙니다:"처럼
# 부정·서술 문장의 앞부분은 이 코퍼스의 formal 종결어미 관례상(`_SENTENCE_
# BOUNDARY` 설명 참고) 거의 항상 "-다"로 끝난다. `_strip_field_label_prefix()`가
# 이 규칙으로 라벨 여부를 판별한다 — 콜론 앞 30자까지 허용(실측 최장 라벨 25자,
# 여유를 둠)하되 "-다"로 끝나면 라벨이 아니라고 보고 그대로 둔다.
_COLON_LABEL_PREFIX = re.compile(r"^([가-힣][가-힣 ]{0,29}):\s*")


def _strip_field_label_prefix(sentence: str) -> str:
    match = _COLON_LABEL_PREFIX.match(sentence)
    if match is None:
        return sentence
    if match.group(1).rstrip().endswith("다"):
        return sentence
    return sentence[match.end() :]


# 위 경계의 첫 대안(마침표 뒤 공백)이 "다.\n- 다만" 같은 자리에서 공백을 먼저 소비하면
# 두 번째 대안이 요구하는 "-앞 공백"이 사라져 대시가 다음 조각 맨 앞에 남는다(실측:
# 210만 조각). 분리 후 선행 "- "만 떼어낸다 — "-120억원"처럼 공백 없이 붙은 부호는
# 경계 정의와 같은 이유로 건드리지 않는다.
_BULLET_PREFIX = re.compile(r"^-\s+")

# 표 셀 파편("보통주식 (주)" 등)은 문장이 아니라 상투구 후보로 부적합하다 — 정확한
# 판별은 불가능하니 최소 길이로만 거른다(임시 휴리스틱, 실측 재검증 필요).
MIN_PHRASE_LENGTH = 8

# 실측(PR #57, A: match_many() 배치 매칭을 실 코퍼스로 돌리다 CUDA OOM 재현) —
# _SENTENCE_BOUNDARY 는 마침표 기준이라, 마침표가 거의 없는 텍스트(예: chunk_disclosure
# 의 "본문(미분할)" 폴백처럼 구조를 못 찾아 태그만 벗겨낸 섹션 전체)는 통째로 "문장 하나"
# 취급된다 — 실제로 32,450자짜리 "문장"이 나왔다. 이런 것이 배치 임베딩에 섞이면 그
# 배치 전체가 그 길이에 맞춰 패딩되면서 어텐션 메모리가 O(길이^2)로 터진다(8GB GPU에서
# 16GB 할당 시도로 크래시 확인됨). 실제 화법 문장은 이 정도로 길 수 없다 — 필터를 통과한
# 정상 후보 중 최댓값이 430자였다(같은 실측) — 그보다 넉넉히 위에 상한을 둔다.
MAX_PHRASE_LENGTH = 500

# 공시 서술형 문장은 거의 항상 이 종결어미들로 끝난다("~하였습니다.", "~할 예정임.",
# "~검토하고 있음." 등). 표 헤더·소유보고서 항목("한 글 김상범", "찬성 퇴임 찬성")은
# 이 패턴으로 끝나지 않는 경우가 실측상 대부분이었다 — 형태소 분석 없이 종결어미
# 자체를 정규식으로 매칭(위 _SENTENCE_BOUNDARY 설계와 같은 이유로 형태소 분석기는
# 안 씀). 마침표는 선택(`\.?`)이다 — "- " 불릿 경계로 갈린 조각은 마침표 없이
# 종결어미로만 끝나는 경우가 실측상 흔했다(위 모듈 docstring "네 번째 보완" 참고).
# 마침표 기준 분리는 lookbehind로 항상 마침표를 왼쪽 조각에 남기므로, 이 완화는
# 기존 마침표 분리 케이스의 동작을 바꾸지 않는다 — 새로 추가된 "- " 분리 케이스만
# 영향을 받는다.
_NARRATIVE_ENDING = re.compile(r"(다|함|음|임|됨)\.?$")

# 재무제표·소유보고서는 숫자 나열이 대부분이라 서술형 종결어미로 끝나더라도(우연히,
# 혹은 "-원임." 같은 표 항목) 숫자 비중이 비정상적으로 높다 — 실측으로 정한 임시
# 임계값(재검증 필요).
_MAX_DIGIT_RATIO = 0.25

# K-IFRS 회계기준 주석 어휘 — A11(scripts/extract_cliche_candidates.py::is_candidate_sentence,
# 담당 A)이 같은 목적으로 이미 쓰고 있던 필터를 프로덕션 경로로 가져온 것이다(모듈
# docstring "여섯 번째 보완"). `disclosure_lit/`은 `scripts/`(패키지 아님)에 의존할 수
# 없어 어휘를 여기 둔다 — 중복 정의가 남으므로, A11이 이 모듈을 import하도록 정리하는
# 건 A와 협의할 후속 과제다.
#
# 두 단계로 나눈 이유(A11 주석 그대로): _STRONG은 회계기준 문서가 아니면 거의 안 쓰는
# 표현이라 1회 등장만으로 제외하고, _WEAK은 일반 서술에도 나올 수 있어(예: "공정가치")
# 2개 이상 겹칠 때만 제외한다.
_KIFRS_STRONG_MARKERS = frozenset(
    {
        "기준서",
        "조기적용",
        "회계정책",
        "리스이용자",
        "리스제공자",
        "사용권자산",
        "상각후원가",
        "유효이자율법",
    }
)
_KIFRS_WEAK_VOCAB = frozenset(
    {
        "금융자산",
        "금융부채",
        "기타포괄손익",
        "공정가치",
        "당기손익",
        "연결재무제표",
        "지분상품",
    }
)

# "【 대표이사 등의 확인 】"류 전각 괄호는 DART 섹션 제목에만 쓰이는 관례적 표기라
# 화법일 수 없다(A11 `_SECTION_TITLE_BRACKETS`와 같은 근거).
_SECTION_TITLE_BRACKETS = re.compile(r"[【】]")


def _is_accounting_note(sentence: str) -> bool:
    """K-IFRS 회계기준 주석 문장인지 판정한다(_KIFRS_* 어휘 설명 참고).

    2026-09-22 팀 리뷰(@9abin) 지적: `term in sentence`가 단어 경계 없는 substring
    매칭이라 "평가기준서식"처럼 무관한 합성어 안에 강한 마커("기준서")가 우연히
    포함되면 일반 문장이 회계주석으로 오분류될 수 있다는 지적이었다 — 검토했다.

    **"단어 경계 체크를 추가"하는 제안은 직접 검증해보니 채택하지 않는 게 맞다.**
    한국어는 조사가 공백 없이 바로 붙는 교착어라(예: "기준서의", "기준서를") 정상적인
    용례 자체가 마커 앞뒤에 한글이 붙어 있는 게 압도적 다수다 — 실측(114개사 코퍼스,
    강한 마커 8종 전체): 매칭 376,268건 중 81.8%(307,840건)가 앞/뒤에 한글이 붙어
    있다("상각후원가측정금융자산", "리스이용자에게" 등 정상적인 회계 문장이 전부
    여기 해당). "앞뒤에 한글이 없어야 매칭"이라는 식의 경계 체크를 넣으면 진짜
    회계주석의 82%를 필터가 놓치게 돼, 오분류를 막으려다 필터 자체를 무력화하는
    꼴이 된다 — 지적된 오탐 시나리오보다 명백히 더 큰 손해다.

    지적된 반례("평가기준서식") 자체도 실제 114개사 코퍼스에 등장하지 않는다(전수
    조사로 확인) — 이론적으로 가능한 시나리오이지 실측으로 확인된 문제는 아니다.
    한국어 형태소 분석 없이 이 이상 정밀하게 구분하는 건 이 모듈이 이미 여러 곳에서
    인정한 한계(`_strip_field_label_prefix()` 관련 설명 참고)와 같은 범주라, 수정
    보류하고 이 판단 근거를 여기 남긴다.
    """
    if _SECTION_TITLE_BRACKETS.search(sentence):
        return True
    if any(term in sentence for term in _KIFRS_STRONG_MARKERS):
        return True
    return sum(1 for term in _KIFRS_WEAK_VOCAB if term in sentence) >= 2


def _is_narrative_sentence(sentence: str) -> bool:
    """표 양식 텍스트(재무제표·소유보고서 등)와 회계기준 주석을 걸러내고 서술형
    문장만 남긴다."""
    if not _NARRATIVE_ENDING.search(sentence):
        return False
    digit_ratio = sum(ch.isdigit() for ch in sentence) / len(sentence)
    if digit_ratio > _MAX_DIGIT_RATIO:
        return False
    return not _is_accounting_note(sentence)


# A11(scripts/extract_cliche_candidates.py, #55)과 같은 원칙 — 여러 무관한 회사가 같은
# 말을 쓰면 업계 표준 화법일 가능성이 높다. 2026-09-21부터는 문자열이 아니라 **클러스터
# 멤버의 회사 합집합**에 적용한다(모듈 docstring "다섯 번째 보완"). 처음엔 클러스터
# 인덱스를 실제로 빌드하는 호출부(demo/app.py::_phrase_bundle())가 `PhraseClusterIndex.
# build(min_companies=...)`로 넘겼는데, 같은 날 후속 변경으로 매칭 성공 여부에서
# 분리했다(모듈 docstring "여섯 번째 보완" 절 끝부분 참고) — 지금은 `dictionary/
# cluster_meaning.py::MIN_COMPANIES_FOR_MEANING`이 이 상수를 재사용해 신뢰도만 매긴다.
MIN_COMPANIES_DEFAULT = 5

# 클러스터링 *전* 사전 필터 — 스케일 제한 전용(실측 PR #57: 필터 없이 고유 후보 525,843개,
# 2026-09-21 재측정: 불릿 제거 후 301,485개 → 2개사 이상 9,885개). 의미 기준 회사 수는
# 위 MIN_COMPANIES_DEFAULT가 클러스터 단위로 다시 센다.
LEXICAL_MIN_COMPANIES = 2

# 목표 섹션 밖 문구에만 적용하는 회사 수 비율 상한 — A11
# (`scripts/extract_cliche_candidates.py::extract_candidates`, 담당 A)의
# `max_company_fraction`을 프로덕션 경로로 가져온 것이다(모듈 docstring "여섯 번째
# 보완"). A11 주석의 근거 그대로: K-IFRS 어휘 목록만으로는 기준서마다 표현이 달라
# 끝없이 단어를 추가해야 하는 두더지잡기가 된다("추정 및 가정은 지속적으로 평가되며…",
# "주요 개정내용은 다음과 같습니다."처럼 회계 어휘가 아예 없는 주석도 많다). 반면 법정
# 의무 문구의 공통 성질은 "회사가 선택하는 게 아니라 거의 전부가 쓴다"는 것이라, 회사
# 수 비율 상한 하나로 어휘 목록 없이 일반화되게 걸러진다.
#
# 값(0.15)은 실측으로 정했다(114개사 코퍼스): 목표 섹션(조회공시·풍문·해명) 출신 문장
# 934개의 회사 수는 **최대 11개사(10%)**, 중앙값 1개사였고, **12개사(11%)를 넘는 문구
# 339개 중 목표 섹션 출신은 0개**였다 — 즉 진짜 IR 화법과 법정 의무 문구는 회사 수로
# 깨끗이 갈린다. 관측 최댓값(10%) 위로 여유를 두되 그 위 구간은 전부 잘라내는 값이다.
# A11의 0.5는 이 코퍼스에선 사실상 아무것도 못 거른다(9,039→9,032, 7개) — 회계주석이
# 44~56개사(39~49%)에 몰려 문턱 바로 아래 있기 때문.
MAX_COMPANY_FRACTION = 0.15

# 이 패턴에 걸리는 섹션(제목 또는 본문 앞부분)의 서술 문장은 사전 필터 없이 풀에 넣는다
# — 루머 대조 결과에 붙는 근거 공시가 바로 이 유형이고, 그 상투구는 회사별 고유 내용과
# 한 문장에 섞여 문자열 필터로는 못 건진다(모듈 docstring "다섯 번째 보완"). 실측
# 199섹션/33개사라 의미 기준 5개사 지지에는 충분하다.
TARGET_SECTION_PATTERN = re.compile(r"조회공시|풍문|해명")
_TARGET_SECTION_HEAD_CHARS = 200

# `cache_store.phrase_fingerprint()`가 참조한다 — 이 파일의 추출 로직(정규식·필터)이
# 바뀌면 올려서 디스크 캐시를 강제로 무효화한다. 예전엔 MIN_COMPANIES_DEFAULT/
# CLUSTER_MIN_SCORE 같은 "파라미터"만 지문에 잡혔고 추출 로직 자체의 버전은 안
# 잡혀서, "네 번째 보완"(위 모듈 docstring)처럼 정규식만 바뀐 경우 기존 캐시가
# 조용히 재사용될 뻔했다. 3 = 불릿 제거 + 후보 풀 재설계(2026-09-21).
# 4 = 개행 경계 + 서식 라벨 제거 + K-IFRS 회계주석 필터(2026-09-21, "여섯 번째 보완").
#
# 2026-09-22 팀 리뷰(@9abin) 지적·수정 — 4를 매긴 뒤(b963ca1)에도 이 브랜치에서
# 추출 로직이 여러 번 더 바뀌었다(콜론 라벨 판별을 화이트리스트→"-다" 종결
# 휴리스틱으로 전환, 회계주석 소제목 `_ACCOUNTING_SUBNOTE_LABEL` 추가와 그
# 두 차례 룩비하인드 수정, 섹션 이중 처리 제거)인데 버전 상수는 그대로였다 —
# 이 PR이 직접 추가한 `cache_store.py` 경고("조용히 재사용됨")가 말하는 상황이
# 이 PR 커밋들 사이에서 재현되고 있었다. 5로 올려 이번 브랜치의 추출 로직
# 변경을 전부 한 번에 반영한다(병합 전 최종 상태 기준).
CANDIDATE_EXTRACTION_VERSION = 5


def load_sections_jsonl(path: Path) -> list[DisclosureSection]:
    """`scripts/collect_disclosures.py`(A)가 쓴 sections.jsonl을 읽는다.

    파일이 없으면 빈 리스트를 반환한다 — 아직 그 종목 코퍼스를 안 모았다는 뜻이라
    에러가 아니다(collect_disclosures.py의 "조용히 안 버리되 없으면 없는 대로" 원칙과
    동일).
    """
    if not path.exists():
        return []
    sections: list[DisclosureSection] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            sections.append(DisclosureSection(**json.loads(line)))
    return sections


def load_all_local_sections(raw_dir: Path) -> list[DisclosureSection]:
    """`data/raw/<corp_code>/sections.jsonl` 전부를 모아 하나의 리스트로 합친다.

    `raw_dir`이 없으면(코퍼스를 아직 하나도 안 모았으면) 빈 리스트 — 인덱스를 빈
    상태로 두는 기존 동작(demo/app.py TODO)과 동일하게 자연스럽게 이어진다.
    """
    if not raw_dir.exists():
        return []
    sections: list[DisclosureSection] = []
    for jsonl_path in sorted(raw_dir.glob("*/sections.jsonl")):
        sections.extend(load_sections_jsonl(jsonl_path))
    return sections


def load_corpus_companies(raw_dir: Path) -> dict[str, str]:
    """`scripts/select_corpus_companies.py`(A10)가 쓴 `corpus_companies.json`에서
    corp_code -> corp_name 매핑을 읽는다.

    `demo/app.py`(회사 이름 표시·종목 선택 드롭다운, C 담당)가 쓰는 용도 — 파일이
    없거나 손상됐으면 빈 dict를 돌려준다(이름 표시/종목 목록만 못 채울 뿐, 나머지
    기능은 계속 동작해야 한다). `load_all_local_sections()`처럼 `demo/app.py`에서
    직접 `import`해서 쓴다 — Streamlit `AppTest`가 스크립트를 매번 새 네임스페이스로
    재실행하는 특성상(파일 상단 `tests/test_demo_app.py` 주석 참고), app.py 안에
    직접 정의된 함수/상수는 monkeypatch로 못 가로채고 이렇게 **외부 모듈에서 import한
    이름**만 테스트에서 안정적으로 바꿔치기할 수 있다.
    """
    path = raw_dir / "corpus_companies.json"
    if not path.exists():
        return {}
    try:
        companies = json.loads(path.read_text(encoding="utf-8"))
        return {c["corp_code"]: c["corp_name"] for c in companies if c.get("corp_code")}
    except Exception:  # noqa: BLE001 - 이름 표시만 못 할 뿐, 데모 전체는 계속 동작
        # 2026-09-22(상세 감사) — 파일이 아예 없는 경우(위 exists() 체크)와 달리
        # 이 분기(파일은 있는데 손상됨)는 예전엔 무로그였다. 드롭다운이 빈 채로
        # 나오는 증상만 보이고 원인(JSON 손상 등)을 추적할 방법이 없었다.
        _logger.warning("%s 파싱 실패 — 종목명 표시 없이 계속 진행", path, exc_info=True)
        return {}


def extract_phrase_candidates(
    sections: list[DisclosureSection], *, min_companies: int = 1
) -> list[str]:
    """섹션 본문을 문장 단위로 쪼개 클러스터링 후보로 만든다. 중복은 제거한다.

    `min_companies`(기본 1, 사실상 필터 없음)는 서로 다른 몇 개 회사에서 등장해야
    후보로 남길지 정한다. 기본값이 1인 이유: 이 함수의 호출부가 둘인데 성격이
    다르다 — `demo/app.py::_phrase_index()`는 코퍼스 전체에서 실제 클러스터 후보를
    뽑는 자리라 회사 빈도 필터가 필요하지만(`MIN_COMPANIES_DEFAULT` 참고),
    `_build_outcome_labels()`는 섹션 하나씩 넘기며 이미 빌드된 클러스터에 매칭만
    하는 자리라 문턱을 걸면(섹션 하나엔 회사가 항상 1개뿐이니) 전부 걸러져 매칭
    자체가 깨진다. 그래서 필터는 옵트인이고, 호출부가 각자 맞는 값을 넘긴다.

    반환 순서는 첫 등장 순이다. `demo/app.py::_match_cluster_for_text()`가 이 순서를
    쓰는 건 신뢰 가능한(`ClusterMeaning.is_reliable`) 후보가 하나도 없을 때의
    폴백뿐이다(R104-1 수정, 2026-09-21 외부 리뷰) — 신뢰 가능한 후보가 있으면
    등장 순서와 무관하게 그게 우선한다.
    """
    seen = extract_phrase_companies(sections)
    return [phrase for phrase, companies in seen.items() if len(companies) >= min_companies]


def _iter_narrative_sentences(section: DisclosureSection) -> Iterator[str]:
    text = _ACCOUNTING_SUBNOTE_LABEL.sub(" ", section.text)
    text = _LIST_LABEL.sub(" ", text)
    for sentence in _SENTENCE_BOUNDARY.split(text):
        sentence = _BULLET_PREFIX.sub("", sentence.strip()).strip()
        sentence = _strip_field_label_prefix(sentence).strip()
        length_ok = MIN_PHRASE_LENGTH <= len(sentence) <= MAX_PHRASE_LENGTH
        if length_ok and _is_narrative_sentence(sentence):
            yield sentence


def extract_phrase_companies(sections: list[DisclosureSection]) -> dict[str, set[str]]:
    """서술형 문장 → 그 문장이 등장한 corp_code 집합. 삽입 순서 = 첫 등장 순."""
    seen: dict[str, set[str]] = {}
    for section in sections:
        for sentence in _iter_narrative_sentences(section):
            seen.setdefault(sentence, set()).add(section.corp_code)
    return seen


def _is_target_section(section: DisclosureSection) -> bool:
    head = section.text[:_TARGET_SECTION_HEAD_CHARS]
    return bool(
        TARGET_SECTION_PATTERN.search(section.section_title) or TARGET_SECTION_PATTERN.search(head)
    )


def select_clustering_pool(
    sections: list[DisclosureSection],
) -> tuple[list[str], dict[str, set[str]]]:
    """클러스터링에 넣을 문구 풀과, 문구별 등장 회사 집합을 돌려준다.

    풀 = 목표 섹션(`TARGET_SECTION_PATTERN`)의 모든 서술 문장 ∪ (`LEXICAL_MIN_COMPANIES`
    개사 이상 `MAX_COMPANY_FRACTION` 이하에 정확히 같은 문자열로 등장한 문장).

    회사 수 상한을 목표 섹션 문장에는 안 거는 이유: 널리 쓰이는 IR 화법이야말로 이
    제품이 찾는 것이라, 거기에 상한을 걸면 목적 자체와 어긋난다. 상한은 "법정 의무라
    거의 전 회사가 쓰는" 목표 섹션 **밖** 문구(회계주석 등)를 걷어내는 용도다
    (`MAX_COMPANY_FRACTION` 주석의 실측 근거 참고).

    회사 집합(풀 밖 문구까지 전부 담음)은 원래 `PhraseClusterIndex.build()`의 클러스터
    생존 필터(`min_companies`)에 썼는데, 그 필터는 같은 날 후속 변경으로 뺐다(모듈
    docstring "회사 빈도 필터 두 단계" 절 참고) — `build()`의 호출부에서는 더 안
    쓰고, 회사 수 판정은 이제 `dictionary/cluster_meaning.py::MIN_COMPANIES_
    FOR_MEANING`이 실제 매칭된 코퍼스를 다시 스캔해서 한다. 그래도 이 반환값은
    API로 남겨둔다(다른 호출부가 pool과 별개로 회사 분포를 알고 싶을 수 있어서).
    순서는 첫 등장 순(`extract_phrase_candidates()`와 동일).
    """
    # 2026-09-22 팀 리뷰(@9abin) 지적·수정 — 예전엔 `extract_phrase_companies(sections)`
    # (전체 섹션 순회)를 부른 뒤, 목표 섹션만 다시 순회하며 `_iter_narrative_sentences()`
    # 를 한 번 더 불렀다. 목표 섹션(~199개)은 전체(82,876개)의 극히 일부라 실측
    # 성능 영향은 미미하지만(비차단 지적), 같은 섹션의 정규식 분리 비용을 두 번
    # 낼 이유가 없어 한 번의 순회로 합쳤다 — `companies_by_phrase`와 `targeted`를
    # 동시에 채운다(결과는 이전과 동일, `extract_phrase_companies()`의 순서 보장도
    # 그대로 유지).
    companies_by_phrase: dict[str, set[str]] = {}
    targeted: set[str] = set()
    for section in sections:
        is_target = _is_target_section(section)
        for sentence in _iter_narrative_sentences(section):
            companies_by_phrase.setdefault(sentence, set()).add(section.corp_code)
            if is_target:
                targeted.add(sentence)
    # 상한이 하한(`LEXICAL_MIN_COMPANIES`)보다 낮아지면 풀이 통째로 비어버린다 —
    # 회사가 몇 개 없는 코퍼스(드라이런, 팀원이 일부만 수집한 상태, 단위 테스트)에서
    # 실제로 발생한다(A11 `max_company_fraction` 주석도 같은 경고). 상한은 하한 밑으로
    # 내려가지 않게 바닥을 깐다 — 회사 수가 충분해지면 비율 상한이 자연히 위로 올라간다.
    total_companies = len({s.corp_code for s in sections})
    max_companies = max(total_companies * MAX_COMPANY_FRACTION, LEXICAL_MIN_COMPANIES)
    pool = [
        phrase
        for phrase, companies in companies_by_phrase.items()
        if phrase in targeted or LEXICAL_MIN_COMPANIES <= len(companies) <= max_companies
    ]
    return pool, companies_by_phrase
