# 07. 디자인 스타일 · 팔레트

> `design-interview` 스킬의 style-picker 단계 산출물 · 상태: confirmed, **v2(2026-09-11 재작업)**
> v1은 독립적으로 설계한 IBM Plex 기반 팔레트였다. 재작업 사유: 사용자가 "노트북에 있는
> 다른 프로젝트들의 상태를 확인해서 재작업하라"고 요청 — 조사 결과 `file-converter`·
> `study-blog` 두 프로젝트가 이미 동일한 **Material 3 토큰 아키텍처**(`--ds-*` 네이밍,
> 동일 spacing/radius/elevation 스케일, 동일 컴포넌트 클래스 구조)를 쓰고 있는 게 확인돼
> 사용자의 기존 퍼스널 디자인 시스템에 맞춰 다시 만들었다. 인터랙티브 프리뷰는
> [공시 리터러시 팔레트 Artifact](https://claude.ai/code/artifact/3a35953c-2b8c-4580-8198-6cb790f02138)
> 참고(v2 반영).
>
> **추가 조사(2026-09-11, 같은 날 후속)**: `Game/textRPG`도 동일 아키텍처를 쓰고 있어
> 확인 — 파일명 컨벤션(`07_visual_style.md`, 이 문서가 그 결과로 개명됨)과 5페이지
> 문서 구조(원칙/컴포넌트/패턴/구현매핑/와이어프레임 + `ds.js` 공용 스크립트)까지
> 그대로 따랐다. 실제 구현은 [`design-system/`](design-system/README.md) 참고 —
> 이 문서(07)는 결정 근거, `design-system/`는 그 결정의 페이지 단위 구현체다.

## 재작업 근거 — 다른 프로젝트 조사 결과

| 프로젝트 | 토큰 구조 | ColorScheme | 폰트 | 핵심 원칙 |
| --- | --- | --- | --- | --- |
| `file-converter` | `docs/design-system/styles.css`, Material 3 `--ds-*` | Vault Teal (`#00696D`) | Pretendard Variable + Roboto Mono | tertiary(초록)를 "변환 완료" 단 하나의 의미에만 격리 |
| `study-blog` | 동일 구조(byte-identical) | Logbook Blue (`#33569C`) | Pretendard Variable + Roboto Mono | tertiary(자주)를 "트러블슈팅" 단 하나의 의미에만 격리 |
| `meeting-stt-app` | 자체 토큰 | 인디고(`#4f46e5`) 단일 | OS 시스템 폰트(의도적 예외 — 한/영 혼용 내부 도구) | "The One Accent Rule" — 액센트 1개, 화면의 10% 이하 |

**공통 원칙(3개 프로젝트 독립적으로 수렴):** 액센트는 하나, 두 번째 색(tertiary)을 쓸
땐 반드시 **단 하나의 의미에만** 엄격히 격리한다. 이건 이번 프로젝트가 v1에서 세운
"신호등 색 금지" 원칙과 근본적으로 같은 규율이다 — 그래서 v1의 결론(단일 액센트,
색으로 긍정/부정 나누지 않음)은 유지하되, **토큰 구조·폰트·색 역할 체계를 기존
퍼스널 시스템에 맞춘다.**

**폰트 재검토:** IBM Plex Sans/Mono KR은 두 프로젝트 어디에도 없는 처음 보는 선택이었다.
Pretendard Variable(한글 가독성) + Roboto Mono(구조화 데이터)가 실제로 2개 프로젝트에서
검증된 조합이라 이쪽으로 교체한다.

## 2026-09-23 적용 범위 — 레트로 입력 단말기

레트로 연출은 입장·입력 화면에 한정하고, 결과 영역은 기존 미니멀 스타일을 유지한다.
입력 단말기의 앰버는 장비 인광색이며 통계 상태를 뜻하지 않는다. 결과 영역의 의미색 규칙과
분리된 `--terminal-*` 토큰을 쓴다. 아래 Ledger Blue 토큰은 결과 영역의 기존 설계 기준이다.

- CRT 내부: 어두운 배경·앰버, 켜짐 1회. 조회 입력과 본문에는 Pretendard를 사용한다.
- 앱 전체 배경은 **이미지 파일 없이 CSS 그라데이션으로 직접 그린다**(벽·책상·단말기 조명
  세 층, `cover`로 비율 유지). 처음에는 외부 사이트의 방 이미지를 썼으나 제3자 저작물이라
  2026-09-29 공개 준비 때 제거했다(`docs/testing/reference-background.md`).
- 전원이 켜진 유리 표면에 잔잡음·3px 주사선·9초마다 지나가는 간섭 띠를 겹친다.
  입력 필드에 포커스가 있으면 잡음과 주사선을 낮추고 간섭 띠를 숨긴다. 글자·위젯은 움직이지 않는다.
  CRT 잡음은 자체 SVG 질감과 CSS로 만든다. 외부 영상·레퍼런스 자산은 사용하지 않는다.
- Galmuri는 장비명·짧은 부팅 안내에만 사용한다. 두 폰트와 OFL 라이선스를 로컬에 번들한다.
- 결과·사전: 밝은 종이색과 진한 본문. 사실의 긍정/부정에 같은 시각적 비중을 적용한다.
- 2026-09-24 조회 기록: 사전 옆 탭에 최대 1200px 분할 화면을 둔다. 어두운 서류 목록과 밝은
  출력물을 나란히 배치하고, 선택된 서류만 앰버 선·배경·네이티브 선택 상태로 구분한다.
  900px 이하에서는 목록 위·문서 아래, 1100px 이하에서는 사실 그룹도 세로 배치한다.
  목록은 데스크톱 660px·작은 화면 240px 이내로 스크롤하며 재열람 연출은 없다.
- 조회를 시작하면 `/result` 전용 페이지로 이동해 밝은 출력물에서 대기·완료 상태를 보여준다.
  결과 페이지에는 CRT 없이 제목·복귀 버튼·출력물을 배치한다. 복귀·직전 결과 버튼은 크림색
  배경에 진한 글자로 대비를 확보한다. 대기 중 비활성 복귀 버튼도 형태와 문구를 유지한다.
- 조회 문장은 16px·줄높이 1.65로 한 번만 보여준다. 사실 대조·맥락 제목은 H2(1.45rem),
  사실 그룹·근거 제목은 H3(1rem)로 구분한다. 본문은 어절 단위로 줄바꿈한다.
- 사실의 근거 번호는 원문 위치로 연결한다. 같은 섹션의 중복 원문을 합쳐 탐색 부담을 줄인다.
- 결과 상단에 크림색 프린터 배출구와 어두운 슬롯·얕은 그림자를 둔다. 새 결과의 종이 한 장만
  1초 동안 아래로 드러내고 본문 요소별 연출은 추가하지 않는다. 직전 결과·오류는 즉시 표시한다.
  기본 결과는 항상 보이는 상태이며 무음·모션 축소를 지원한다.
- 주요 버튼과 종목 선택 펼치기 버튼은 최소 44px 높이, 예시 칩은 여러 줄을 허용한다.
  종목 선택의 삭제(X) 버튼은 숨기고 다른 종목으로 변경하는 동작을 유지한다.
  320px·400px에서 가로 넘침 없이 결과 컬럼을 세로로 쌓는다.
- 폰트 파일은 앱의 `static/fonts/`에서 제공한다. 아래 CDN 예시는 이전 설계 참고용이다.
- 테마의 기본 강조색은 앰버로 설정해 입력 포커스에 기본 빨강이 노출되지 않게 한다.
- 모션 축소 설정에서는 켜짐·잡음·간섭 띠 애니메이션을 모두 끄고 약한 정지 질감만 남긴다.
  `?power=on`은 전원 켜짐 연출을 생략하고 바로 입력하는 경로다. 켜진 화면의 질감은 적용된다.

구현: `demo/terminal.py`, `demo/static/retro.css`, `demo/static/power-on.css`, `demo/static/print-feed.css`,
`.streamlit/config.toml`. Streamlit 1.63 이상을 기준으로 실제 DOM과 모바일 320px·400px을 검증한다.

## Step 1 — 업종·타깃 판단 (v1과 동일, 재검토 결과 유지)

고객이 이 화면에서 가장 먼저 확인하고 싶은 것은 "믿을 만한가"이지 "요즘 감각 있는가"나
"기억에 남는가"가 아니다. `style-picker/references/style-catalog.md`의 신뢰감·전문성
카테고리로 고정, **미니멀리즘** 확정(변경 없음).

## Step 4 — 핵심 설계 원칙

**왜 신호등 색이 없는가:** 국내 증권 UI 관행상 빨강/초록은 하락/상승·매도/매수를 뜻한다.
뒷받침/배치 사실에 그 색을 쓰면 화면이 암묵적 매매 시그널을 발신해 규제 방어선(CLAUDE.md
주의점 1·2, PRD-MVP.md 4절)을 UI 레벨에서 어기게 된다.

**퍼스널 시스템과의 관계 — 의도적 이탈 지점 1곳:** `file-converter`의 STATE-002는 5개
도메인 상태 전부에 배경색을 입힌다(queued/converting/done/failed/skipped 각각 고유
bg/fg). 이 프로젝트도 `ClusterStats` 유의성 상태(STATE-002)는 같은 패턴을 따르되,
**팩트체크 긍정/부정(FactCheckItem.polarity)에는 절대 이 패턴을 적용하지 않는다** —
적용하면 사실상 매수/매도 신호등이 되기 때문이다. 이건 기존 시스템을 따르지 않는 게
아니라, 기존 시스템의 "격리 규율"을 이 프로젝트의 규제 제약에 맞게 더 엄격히 적용한
것이다.

## 컬러 토큰 — ColorScheme "Ledger Blue"

Material 3 역할 체계, `--ds-*` 네이밍(`file-converter`/`study-blog`와 동일 아키텍처).
"Ledger"(공식 장부/기록)는 공시(disclosure filing)라는 도메인 자체를 가리키는 이름.

### Light

```css
:root{
  --ds-primary:#2F5FA8; --ds-on-primary:#FFFFFF;
  --ds-primary-container:#D7E3FF; --ds-on-primary-container:#001B3D;
  --ds-secondary:#55607B; --ds-on-secondary:#FFFFFF;
  --ds-secondary-container:#DAE2F9; --ds-on-secondary-container:#121C33;
  --ds-tertiary:#6E5B1E; --ds-on-tertiary:#FFFFFF;   /* 격리: "표본 부족 경고" 전용 */
  --ds-tertiary-container:#F9E3A8; --ds-on-tertiary-container:#241A00;
  --ds-error:#BA1A1A; --ds-on-error:#FFFFFF;          /* 격리: 시스템/API 오류 전용 */
  --ds-error-container:#FFDAD6; --ds-on-error-container:#410002;
  --ds-surface:#FBFAFD; --ds-on-surface:#1A1B20;
  --ds-surface-variant:#E1E2EC; --ds-on-surface-variant:#45464F;
  --ds-outline:#767680; --ds-outline-variant:#C6C6D0;
  --ds-surface-container-lowest:#FFFFFF; --ds-surface-container-low:#F5F3F9;
  --ds-surface-container:#EFEDF4; --ds-surface-container-high:#E9E7EE;
  --ds-surface-container-highest:#E3E2E9;
  --ds-inverse-surface:#2F3036; --ds-inverse-on-surface:#F1F0F7;
  --ds-inverse-primary:#B0C6FF;

  /* 도메인 상태 색 — STATE-002 (ClusterStats 유의성) 2상태만. 팩트체크 긍정/부정엔 미적용(위 참고) */
  --ds-st-significant-bg:#EFEDF4;    --ds-st-significant-fg:#1A1B20;  /* 유의 — 중립, 수치만 강조 */
  --ds-st-insufficient-bg:#F9E3A8;   --ds-st-insufficient-fg:#241A00; /* 표본부족 — tertiary 유일 사용처 */

  --ds-font-plain:"Pretendard Variable",Pretendard,"Apple SD Gothic Neo","Noto Sans KR",system-ui,sans-serif;
  --ds-font-mono:"Roboto Mono",ui-monospace,Menlo,monospace;

  --ds-space-1:4px; --ds-space-2:8px; --ds-space-3:12px; --ds-space-4:16px;
  --ds-space-5:20px; --ds-space-6:24px; --ds-space-7:32px; --ds-space-8:40px;
  --ds-radius-xs:4px; --ds-radius-sm:8px; --ds-radius-md:12px;
  --ds-radius-lg:16px; --ds-radius-xl:28px; --ds-radius-full:999px;
  --ds-elev-1:0 1px 2px rgba(0,0,0,.30),0 1px 3px 1px rgba(0,0,0,.15);
  --ds-elev-2:0 1px 2px rgba(0,0,0,.30),0 2px 6px 2px rgba(0,0,0,.15);
}
```

### Dark

```css
[data-ds-theme="dark"]{
  --ds-primary:#B0C6FF; --ds-on-primary:#002C68;
  --ds-primary-container:#17448E; --ds-on-primary-container:#D7E3FF;
  --ds-secondary:#BEC6E4; --ds-on-secondary:#283149;
  --ds-secondary-container:#3D4661; --ds-on-secondary-container:#DAE2F9;
  --ds-tertiary:#DCC56F; --ds-on-tertiary:#3B2E00;
  --ds-tertiary-container:#554400; --ds-on-tertiary-container:#F9E3A8;
  --ds-error:#FFB4AB; --ds-on-error:#690005;
  --ds-error-container:#93000A; --ds-on-error-container:#FFDAD6;
  --ds-surface:#121317; --ds-on-surface:#E3E2E9;
  --ds-surface-variant:#45464F; --ds-on-surface-variant:#C6C6D0;
  --ds-outline:#90909A; --ds-outline-variant:#45464F;
  --ds-surface-container-lowest:#0D0E13; --ds-surface-container-low:#1A1B20;
  --ds-surface-container:#1E1F25; --ds-surface-container-high:#292A2F;
  --ds-surface-container-highest:#34343A;
  --ds-inverse-surface:#E3E2E9; --ds-inverse-on-surface:#2F3036;
  --ds-inverse-primary:#2F5FA8;

  --ds-st-significant-bg:#1E1F25;    --ds-st-significant-fg:#E3E2E9;
  --ds-st-insufficient-bg:#554400;   --ds-st-insufficient-fg:#F9E3A8;
}
```

## 타이포그래피

| 역할 | 서체 | 근거 |
| --- | --- | --- |
| 헤딩/본문 | Pretendard Variable (400/500/600/700) | `file-converter`·`study-blog`와 동일 — 검증된 한글 웹폰트 조합, 새 선택 아님 |
| 데이터/코드성 텍스트 | Roboto Mono | 접수번호(rcept_no)·종목코드(corp_code)·통계 수치 등 이 도메인의 구조화 값. 두 프로젝트와 동일 |

```css
@import url("https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable-dynamic-subset.min.css");
@import url("https://fonts.googleapis.com/css2?family=Roboto+Mono:wght@400;500;700&display=swap");
```

## Streamlit 적용

`config.toml`의 `[theme]`는 커스텀 웹폰트를 직접 못 받는다(제한된 키워드만) — 아래
두 가지를 함께 적용해야 실제로 Pretendard가 반영된다.

```toml
# .streamlit/config.toml
[theme]
base = "light"
primaryColor = "#2F5FA8"
backgroundColor = "#FBFAFD"
secondaryBackgroundColor = "#EFEDF4"
textColor = "#1A1B20"
font = "sans serif"
```

```python
# demo/app.py 상단, st.set_page_config() 다음
st.markdown(
    """
<style>
@import url("https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable-dynamic-subset.min.css");
html, body, [class*="css"] { font-family: "Pretendard Variable", sans-serif; }
</style>
""",
    unsafe_allow_html=True,
)
```

## 적용 규칙 체크리스트

- [ ] 포인트 컬러(`--ds-primary`)는 버튼·링크·포커스 상태에만 — 사실 카드 안에는 쓰지 않는다
- [ ] `--ds-tertiary`는 오직 "표본 부족 경고" 한 곳에만 쓴다 — 다른 곳에 재사용 금지(격리 규율)
- [ ] `--ds-error`는 오직 시스템/API 오류(예: DART 호출 실패)에만 — 데이터 자체의 긍정/부정과 혼용 금지
- [ ] 긍정/부정 사실 카드는 항상 같은 배경·테두리·크기로 나란히 배치한다(REQ-F-006) — `--ds-st-*` 상태색 패턴을 여기엔 적용하지 않는다(의도적 이탈, 위 참고)
- [ ] 등급 배지·별점·게이지 형태의 컴포넌트는 이 시스템에 존재하지 않는다
- [ ] 결과 영역은 미니멀리즘으로 유지한다. 레트로 예외 범위는 위 입장·입력 단말기에 한정한다

## 구현 문서

현재 화면 흐름·상태는 [02_ui_flow.md](02_ui_flow.md), [03_screen_contract.md](03_screen_contract.md),
[05_state_machine.md](05_state_machine.md)를 따른다. `design-system/`의 HTML은 초기 설계
아카이브로 표시해 현행 화면 계약과 구분한다.
