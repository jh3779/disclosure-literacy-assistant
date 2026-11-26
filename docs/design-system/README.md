# 공시 리터러시 어시스턴트 — 초기 디자인 시스템 아카이브 (v2)

> **2026-09-23: 아래 HTML 5개는 9월 11일 초기 설계의 보관본이다.** 예측형 통계 카드,
> 종목 미특정 경로, 매칭 실패 시 생략 규칙은 현재 화면과 다르므로 신규 구현에 적용하지 않는다.
> 현행 기준은 [화면 계약](../03_screen_contract.md), [상태 머신](../05_state_machine.md),
> [레트로 적용 범위](../07_visual_style.md)와 `disclosure_lit/demo/` 구현이다.
>
> 2026-09-11 · Material 3 기반 · ColorScheme **"Ledger Blue"** (공식 장부/공시 도메인 → 차분한 블루)
> 이 문서 세트는 **시각 토큰·컴포넌트·상태 표현·화면 목업·구현 매핑만** 소유한다.
> 화면 계약·데이터·상태 전이의 정본은 [`../`](../00_project_brief.md)의 스펙 문서(00~07)다.
>
> **v2 재작업**: 이 노트북의 다른 프로젝트(`file-converter`·`study-blog`·`textRPG`)가
> 이미 쓰는 Material 3 `--ds-*` 토큰 아키텍처와 5페이지 문서 구조에 맞춰 재작업했다 —
> 자세한 조사 근거는 [`../07_visual_style.md`](../07_visual_style.md) 상단 참고.

## 보는 법

브라우저로 `design-system.html`을 열면 상단 네비로 5개 페이지를 오간다. 우상단 ◐ 버튼으로 라이트/다크 확인.

```bash
open docs/design-system/design-system.html
```

CDN 폰트(Pretendard·Roboto Mono·Material Symbols)를 쓰므로 인터넷 연결이 필요하다.
Artifact 등 CSP가 있는 환경에 올릴 땐 `styles.css`의 `@import` 폰트를 시스템 폰트
스택으로 교체할 것(Google Fonts만 허용되는 환경이면 Noto Sans KR로 대체 — 실제로
[공시 리터러시 팔레트 Artifact](https://claude.ai/code/artifact/3a35953c-2b8c-4580-8198-6cb790f02138)가 이 대체를 적용한 사례).

| 페이지 | 내용 |
| --- | --- |
| [design-system.html](design-system.html) | 1 · 디자인 원칙 5개, Ledger Blue Light/Dark, 상태색 2종, 타이포(Pretendard+Roboto Mono), 간격/모양/고도/모션 |
| [components.html](components.html) | 2 · 입력창, 확인 버튼, 팩트체크 카드, 근거 원문, 화법통계 카드, 스피너, 샘플 배지 + **제외 목록** |
| [patterns.html](patterns.html) | 3 · SCR-001 상태 매트릭스(6상태), 화법통계 3변형, 문안 규칙, 접근성 표 |
| [streamlit-mapping.html](streamlit-mapping.html) | 4 · 토큰→config.toml/CSS, 컴포넌트→Streamlit 위젯, 상태머신→분기, pipeline.py 연결 + **Conflict List** |
| [wireframes.html](wireframes.html) | 5 · 흐름도 + 브라우저 탭 목업 6종(idle/loading/result-fact/result-phrase-only/result-empty/sample) |

## 핵심 규칙 요약

- tertiary(amber)는 **"표본 부족 경고" 한 의미에만** 사용. 팩트체크 긍정/부정에는 어떤
  상태색도 입히지 않는다 — 국내 증권 UI의 빨강/초록(하락/상승) 관행과 겹치면 암묵적
  매매 시그널이 되기 때문(자본시장법상 유사투자자문업 회피, PRD-MVP.md 4절).
- 등급·점수·별점·게이지·신호등 컴포넌트는 이 시스템에 존재 자체가 금지.
- 화법통계 매칭 실패는 카드 자체를 생략 — "매칭 없음" 문구조차 띄우지 않는다(Issue #32/DEC-005).
- 강조 굵기는 SemiBold(600) — 한글 Medium(500) 금지. 수치·코드성 값은 Roboto Mono.

## 미결(Conflict List) — 임의 확정하지 않음

OQ-001(collect_disclosures가 rcept_dt 미반영) · OQ-004(정확한 일정 미정) ·
OQ-005(대량 코퍼스 영속화 방식). 전체는 [streamlit-mapping.html](streamlit-mapping.html)
M-05와 [`../06_open_questions.md`](../06_open_questions.md).

## 버전 이력

- **v1 (2026-09-11)**: IBM Plex Sans/Mono KR 기반 독자 팔레트. 단일 페이지 구성.
- **v2 (2026-09-11)**: `file-converter`/`study-blog`/`textRPG` 조사 후 Material 3
  `--ds-*` 아키텍처 + Pretendard/Roboto Mono로 재작업. 5페이지 구조(원칙/컴포넌트/
  패턴/구현매핑/와이어프레임) + `ds.js` 공용 스크립트로 확장. `07_design_style.md` →
  `07_visual_style.md`로 파일명도 textRPG 컨벤션에 맞춰 변경.
