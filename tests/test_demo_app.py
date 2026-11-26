"""demo/app.py 렌더링 시나리오 — streamlit.testing.v1.AppTest.

PR #33 리뷰 지적(@powercodingman): PR 설명에 AppTest로 수동 검증했다고 적었는데 커밋에
테스트 파일이 없었다. `_build_card`의 분기(문구단독 vs 팩트체크+화법통계)나 polarity
렌더링이 나중에 깨져도 CI가 못 잡는다는 지적을 반영해 커밋한다.

**monkeypatch 대상에 대한 메모**: `AppTest.from_file()`은 대상 스크립트(`demo/app.py`)
자체는 매번 새 네임스페이스로 다시 실행하지만, 그 스크립트가 `from X import Y`로 가져오는
`X` 모듈은 平소처럼 `sys.modules` 캐시를 그대로 쓴다. 그래서 `disclosure_lit.demo.app`
쪽 이름을 patch해봐야 다음 실행에서 덮어써진다 — 반드시 **소스 모듈**
(`disclosure_lit.report.card.build_report`)의 속성을 patch해야 새로 exec되는
`demo/app.py`의 `from disclosure_lit.report.card import build_report` 문이 그 값을
집어간다.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from unittest.mock import patch

import pytest
import streamlit as st
from streamlit.proto.WidgetStates_pb2 import WidgetState
from streamlit.runtime.scriptrunner import get_script_run_ctx
from streamlit.testing.v1 import AppTest
from streamlit.testing.v1.element_tree import ButtonGroup
from streamlit.testing.v1.local_script_runner import LocalScriptRunner

from disclosure_lit.config import settings
from disclosure_lit.dart.client import CorpCodeEntry
from disclosure_lit.dart.ingest import DisclosureSection
from disclosure_lit.demo import app as app_module
from disclosure_lit.demo.app import (
    _EXAMPLE_QUERIES,
    _MIN_QUERY_LENGTH,
    _company_query_hint,
    _corp_code_index,
    _corpus_scope_summary,
    _dart_source_url,
    _match_cluster_for_text,
)
from disclosure_lit.dictionary import candidates as candidates_module
from disclosure_lit.dictionary.cluster_meaning import ClusterMeaning
from disclosure_lit.dictionary.glossary import GLOSSARY
from disclosure_lit.dictionary.stats import ClusterStats
from disclosure_lit.factcheck.claim import Claim
from disclosure_lit.factcheck.verify import FactCheckItem, FactCheckResult
from disclosure_lit.report import card
from tests.doubles import _FakeIndex

# AppTest.from_file()의 상대경로는 "이 테스트 파일 기준"으로 풀린다(호출부 기준이지
# CWD 기준이 아님) — 절대경로로 고정해서 pytest를 어느 디렉터리에서 돌려도 동작하게 한다.
APP_PATH = str(Path(__file__).resolve().parent.parent / "disclosure_lit" / "demo" / "app.py")

# 2026-09-18: `AppTest`의 기본 스크립트 실행 제한(3초)이 사전 탭(용어 20개 렌더링,
# PR #98) + 종목 선택/예시 칩/온보딩(PR #100)이 합쳐진 뒤로는 부하가 있는 환경(여러
# 테스트가 동시에 도는 CI 등)에서 종종 못 지켜졌다(`RuntimeError: AppTest script run
# timed out after 3(s)`) — 각 테스트를 단독으로 돌리면 항상 통과해서 로직 문제가
# 아니라 순수 실행 시간 여유 부족으로 판단했다. 실제 프로덕션 Streamlit 앱에는
# 이런 제한이 없다(테스트 하네스 전용 안전장치) — 넉넉히 올려서 flaky를 없앤다.
_APP_TEST_TIMEOUT = 15


_TEST_CORP_NAMES = {"00126380": "삼성전자"}


@pytest.fixture(autouse=True)
def _no_local_corpus(monkeypatch: pytest.MonkeyPatch) -> None:
    """`data/raw/`에 로컬 코퍼스가 실제로 쌓여 있어도 이 테스트들은 영향받으면 안 된다.

    `_company_index()`/`_phrase_index()`(demo/app.py)가 `load_all_local_sections()`로
    로컬 코퍼스를 읽어 실제 임베딩(`sentence-transformers`, `local` extra 필요)까지
    시도한다 — 개발자가 `scripts/collect_disclosures.py`를 로컬에서 돌려본 적 있으면
    이 테스트들이 환경에 따라 실패(모듈 없음)하거나 값이 달라진다. 파일 존재 여부에
    의존하지 않도록 항상 빈 코퍼스를 리턴하게 고정한다. 이 모듈의 다른 monkeypatch와
    같은 원칙(소스 모듈을 patch — 파일 상단 docstring 참고)으로 `demo/app.py`가
    가져오는 원본(`disclosure_lit.dictionary.candidates`)을 patch한다.

    `_corp_names()`(2026-09-18 종목 선택 드롭다운 추가)도 같은 이유로 고정한다 —
    실제 `data/raw/corpus_companies.json` 존재 여부에 따라 `main()`의 드롭다운
    옵션이 달라지면 이 파일의 다른 테스트들(`_run()` 경유)이 환경에 따라 깨진다.
    `_corp_names`가 `@st.cache_resource`라 매 테스트 전에 `.clear()`도 같이
    해야 한다 — 안 그러면 어떤 테스트가 먼저 캐시를 채웠는지에 따라 뒤 테스트가
    그 값을 그대로 물려받는다(`TestCorpNames`의 `_clear_cache_resource`와 동일한
    문제, 여기서는 전체 파일 범위로 미리 막는다).
    """
    monkeypatch.setattr(candidates_module, "load_all_local_sections", lambda _raw_dir: [])
    monkeypatch.setattr(candidates_module, "load_sections_jsonl", lambda _path: [])
    monkeypatch.setattr(
        candidates_module, "load_corpus_companies", lambda _raw_dir: dict(_TEST_CORP_NAMES)
    )
    st.cache_resource.clear()
    st.cache_data.clear()


def _section(text: str = "본문") -> DisclosureSection:
    return DisclosureSection(
        rcept_no="R1", corp_code="00126380", form_type="유상증자", section_title="제목", text=text
    )


def _confirm_button(at: AppTest):
    """ "확인" 버튼을 라벨로 찾는다 — 2026-09-18에 예시 칩 버튼들이 앞에 추가되면서
    `at.button[0]`이 더 이상 "확인"이 아니게 됐다(칩 개수가 바뀌면 인덱스도 같이
    바뀌는 걸 막기 위해 위치가 아니라 라벨로 찾는다)."""
    for button in at.button:
        if button.label == "확인":
            return button
    raise AssertionError('"확인" 버튼을 찾지 못했습니다')


class _ClearedButtonGroup(ButtonGroup):
    """선택을 비운 위젯 상태를 보내는 `ButtonGroup` — `_clear_pills()` 전용."""

    @property
    def _widget_state(self) -> WidgetState:
        ws = WidgetState()
        ws.id = self.id
        ws.string_array_value.data[:] = []
        return ws


def _clear_pills(at: AppTest, idx: int = 0) -> AppTest:
    """단일 선택 `st.pills`의 선택을 해제하고 재실행한다.

    Streamlit 1.63 AppTest에는 단일 선택 pills를 해제하는 방법이 없다 —
    `set_value(None)`은 `value`가 None이면 세션의 이전 값을 다시 읽어 그대로 보내고,
    `set_value([])`는 `"[]"` 문자열로 포맷된다. 브라우저가 해제할 때 보내는 것과 같은
    "빈 선택" 위젯 상태를 직접 보낸다.
    """
    at.pills[idx].__class__ = _ClearedButtonGroup
    return at.run()


class _NavigationAppTest(AppTest):
    """1.63 AppTest가 switch_page 후 누락하는 현재 페이지를 navigation 메시지로 보존한다."""

    def _run(self, *args, **kwargs):
        pages = []
        original = LocalScriptRunner.run

        def record_page(runner, *runner_args, **runner_kwargs):
            tree = original(runner, *runner_args, **runner_kwargs)
            pages.extend(
                msg.navigation.page_script_hash
                for msg in runner.forward_msgs()
                if msg.HasField("navigation")
            )
            return tree

        with patch.object(LocalScriptRunner, "run", record_page):
            result = super()._run(*args, **kwargs)
        if pages:
            self._page_hash = pages[-1]
        return result


def _app(*, powered: bool = True) -> AppTest:
    """조회 테스트는 전원 연출을 건너뛰고 기존 입력 동작을 그대로 검증한다."""
    at = _NavigationAppTest(APP_PATH, default_timeout=_APP_TEST_TIMEOUT)
    if powered:
        at.query_params["power"] = "on"
    return at


class TestTerminalEntry:
    def test_initial_visit_shows_power_button_without_query_widgets(self) -> None:
        at = _app(powered=False).run()
        assert not at.exception
        assert not at.selectbox
        assert not at.text_area
        assert any(button.label == "전원 켜기" for button in at.button)

    def test_power_button_opens_input_and_keeps_it_open_on_rerun(self) -> None:
        at = _app(powered=False).run()
        power = next(button for button in at.button if button.label == "전원 켜기")
        power.click().run()
        assert not at.exception
        assert at.selectbox[0].label == "종목 선택"
        at.text_area[0].input("사용자가 작성한 공시 확인 문장입니다.").run()
        assert at.text_area[0].value == "사용자가 작성한 공시 확인 문장입니다."
        assert not any(button.label == "전원 켜기" for button in at.button)

    def test_power_query_parameter_opens_input_immediately(self) -> None:
        at = _app().run()
        assert not at.exception
        assert at.text_area[0].label == "루머 또는 공시 문장"
        assert not any(button.label == "전원 켜기" for button in at.button)

    def test_other_query_parameter_values_do_not_skip_power(self) -> None:
        at = _app(powered=False)
        at.query_params["power"] = "off"
        at.run()
        assert not at.exception
        assert not at.text_area

    def test_glossary_is_available_before_power_on(self) -> None:
        at = _app(powered=False).run()
        assert len(at.tabs) == 3
        assert len(at.tabs[1].markdown) == len(GLOSSARY)


def _run(
    monkeypatch: pytest.MonkeyPatch,
    build_report_result: object,
    text: str = "충분히 긴 아무 문장입니다",  # 2026-09-18: _MIN_QUERY_LENGTH(8자) 통과용
) -> AppTest:
    if isinstance(build_report_result, Exception):

        def raiser(*args: object, **kwargs: object) -> None:
            raise build_report_result

        monkeypatch.setattr(card, "build_report", raiser)
    else:
        monkeypatch.setattr(card, "build_report", lambda *a, **k: build_report_result)

    at = _app()
    at.run()
    # 2026-09-18: 종목 선택 드롭다운이 필수라(main()), 먼저 선택해야 확인 버튼이
    # 실제 조회를 진행한다 — `_no_local_corpus`가 고정한 `_TEST_CORP_NAMES`에서
    # 하나를 고른다. 어떤 회사가 뽑혔는지는 이 테스트들의 관심사가 아니다(전부
    # `card.build_report` 자체를 monkeypatch해서 인자를 무시하고 고정값을 반환함).
    at.selectbox[0].select(next(iter(_TEST_CORP_NAMES.values()))).run()
    at.text_area[0].input(text).run()
    _confirm_button(at).click().run()
    return at


class TestPhraseOnlySearch:
    @pytest.mark.parametrize(
        "status, reason, message, element",
        [
            ("abstained", "low_relevance", "관련성이 부족", "info"),
            ("error", "guard_failed", "분석을 완료하지 못", "error"),
            ("error", "extraction_failed", "분석을 완료하지 못", "error"),
            ("error", "unresolved_company", "회사를 확인하지 못", "warning"),
        ],
    )
    def test_factcheck_failure_and_abstention_have_distinct_messages(
        self, monkeypatch, status, reason, message, element
    ):
        result = card.ReportCard(
            fact_check=FactCheckResult(claim="루머", items=[], status=status, reason=reason),
            phrase_stats=None,
        )
        at = _run(monkeypatch, result)
        assert not at.exception
        assert any(message in item.value for item in getattr(at, element))
        if status == "error":
            assert not any("관련 공시를 찾지 못" in item.value for item in at.info)

    """종목 미특정 — 화법 문구 단독 검색 경로.

    2026-09-17: `card.phrase_stats`(상투구 클러스터 기반 사후 이벤트 통계)는 클러스터가
    미래 이벤트를 예측한다는 전제가 held-out 검증으로 반증돼 더는 렌더링하지 않는다
    (demo/app.py `main()` 주석 참고) — 그래서 `phrase_stats` 값과 무관하게 항상
    subheader가 안 뜨는 게 지금은 맞는 동작이다."""

    def test_phrase_stats_no_longer_rendered(self, monkeypatch: pytest.MonkeyPatch) -> None:
        card_result = card.ReportCard(
            fact_check=None,
            phrase_stats=ClusterStats(
                cluster_id="C1",
                sample_count=9,
                outcome_label="감자",
                outcome_count=4,
                is_significant=True,
                exceeds_baseline=True,
            ),
        )
        at = _run(monkeypatch, card_result, text="선제적 재무구조 개선")

        assert not at.exception
        # fact_check=None이라 "공시 사실 대조"는 안 뜨고, 옛 "이 표현의 과거 패턴"도
        # 안 뜬다. 남는 건 새 맥락 섹션 하나뿐이다(아래 test에서 내용까지 확인).
        assert "이 표현의 과거 패턴" not in {s.value for s in at.header}
        assert "공시 사실 대조" not in {s.value for s in at.header}
        assert not [c for c in at.caption if "🧪" in c.value]  # 샘플 아님

    def test_no_match_explains_why_instead_of_silence(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """2026-09-22 UX 점검으로 Issue #32/DEC-005의 "매칭 실패면 섹션 생략" 합의를
        좁혔다 — **섹션은 띄우되 이유를 한 줄 적는다.**

        빈 화면만 남기면 "고장인지 해당 없음인지" 사용자가 구분할 수 없는데, 이 화면은
        R104-1(신뢰도 낮은 클러스터에 가로막혀 25건 중 8건만 노출)을 겪은 전력이 있어
        그 구분이 특히 중요하다. 없는 걸 없다고 말하는 것이므로 "표본 부족 시 유의하지
        않음을 명시한다"(CLAUDE.md 주의점 3)와도 같은 방향이다."""
        card_result = card.ReportCard(fact_check=None, phrase_stats=None)
        at = _run(monkeypatch, card_result)

        assert not at.exception
        assert "🔍 이 표현이 가리키는 맥락" in {s.value for s in at.header}
        assert any("찾지 못했습니다" in i.value for i in at.info)


class TestFactcheckCombinedWithPhraseStats:
    """종목 특정 — 팩트체크 + (매칭되면) 화법통계 결합 경로.

    2026-09-17: `phrase_stats` 기반 "이 표현의 과거 패턴"은 더는 렌더링하지 않는다
    (클래스 docstring 참고 — TestPhraseOnlySearch와 동일한 이유). 새 재설계
    (`_render_meaning_map`)는 실제 코퍼스·임베딩이 필요해 이 경량 AppTest 스위트가
    쓰는 빈 코퍼스 픽스처(`_no_local_corpus`)에서는 자연히 아무것도 안 그린다."""

    def test_renders_factcheck_section_positive_negative_columns(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fact_check = FactCheckResult(
            claim="유상증자 루머",
            items=[
                FactCheckItem(
                    fact="뒷받침 사실", supporting_section=_section("근거1"), polarity="positive"
                ),
                FactCheckItem(fact="배치 사실", supporting_section=None, polarity="negative"),
            ],
        )
        card_result = card.ReportCard(fact_check=fact_check, phrase_stats=None)
        at = _run(monkeypatch, card_result, text="삼성전자 유상증자 루머")

        assert not at.exception
        # 맥락 섹션은 빈 코퍼스 픽스처에서 매칭이 안 돼 "찾지 못했습니다" 안내만 뜬다
        # (2026-09-22부터 조용히 생략하지 않는다).
        assert {s.value for s in at.header} == {
            "공시 사실 대조",
            "🔍 이 표현이 가리키는 맥락",
        }
        markdown_values = [m.value for m in at.markdown]
        assert "- 뒷받침 사실" in markdown_values
        assert "- 배치 사실" in markdown_values

    def test_empty_items_shows_info_not_crash(self, monkeypatch: pytest.MonkeyPatch) -> None:
        card_result = card.ReportCard(
            fact_check=FactCheckResult(claim="루머", items=[]), phrase_stats=None
        )
        at = _run(monkeypatch, card_result)
        assert not at.exception
        assert any("찾지 못했습니다" in i.value for i in at.info)


class TestDartSourceLink:
    """원문 링크 — 근거 섹션이 있는 사실마다 실제 DART 조회 페이지 링크를 보여준다.
    rcept_no만 있으면 구성 가능해서 별도 저장 없이 그 자리에서 만든다(gold set의
    source_url과 같은 URL 형식)."""

    def test_dart_source_url_format(self) -> None:
        assert (
            _dart_source_url("20260213800343")
            == "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260213800343"
        )

    def test_link_rendered_for_fact_with_supporting_section(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fact_check = FactCheckResult(
            claim="유상증자 루머",
            items=[
                FactCheckItem(
                    fact="뒷받침 사실", supporting_section=_section("근거1"), polarity="positive"
                ),
            ],
        )
        card_result = card.ReportCard(fact_check=fact_check, phrase_stats=None)
        at = _run(monkeypatch, card_result, text="삼성전자 유상증자 루머")

        assert not at.exception
        markdown_values = [m.value for m in at.markdown]
        assert any(
            "[DART 원문 보기](https://dart.fss.or.kr/dsaf001/main.do?rcpNo=R1)" in m
            for m in markdown_values
        )

    def test_no_link_when_no_supporting_section(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fact_check = FactCheckResult(
            claim="루머",
            items=[FactCheckItem(fact="배치 사실", supporting_section=None, polarity="negative")],
        )
        card_result = card.ReportCard(fact_check=fact_check, phrase_stats=None)
        at = _run(monkeypatch, card_result, text="삼성전자 루머")

        assert not at.exception
        assert not any("DART 원문 보기" in m.value for m in at.markdown)


class TestResultReadingFlow:
    def test_each_fact_references_its_numbered_source(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sections = [
            dataclasses.replace(_section("발행 결정 원문"), section_title="발행 결정"),
            dataclasses.replace(
                _section("발행 조건 원문"), rcept_no="R2", section_title="발행 조건"
            ),
        ]
        result = FactCheckResult(
            claim="유상증자 루머",
            items=[
                FactCheckItem(
                    fact="발행 결정 사실", supporting_section=sections[0], polarity="positive"
                ),
                FactCheckItem(
                    fact="조건 미정 사실", supporting_section=sections[1], polarity="negative"
                ),
            ],
        )
        at = _run(monkeypatch, card.ReportCard(fact_check=result, phrase_stats=None))
        assert not at.exception
        for number, (column, section) in enumerate(zip(at.columns, sections, strict=True), start=1):
            assert any(f"[근거 {number}](#source-{number})" in c.value for c in column.caption)
            source = next(e for e in at.expander if e.label.startswith(f"근거 {number} ·"))
            assert section.section_title in source.label
            assert any(section.text in m.value for m in source.markdown)

    def test_identical_sources_are_shared_but_other_sections_in_same_filing_remain(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        original = _section("동일한 근거 본문")
        another_section = dataclasses.replace(
            original, section_title="다른 절", text="다른 근거 본문"
        )
        result = FactCheckResult(
            claim="유상증자 루머",
            items=[
                FactCheckItem(fact="사실 A", supporting_section=original, polarity="positive"),
                FactCheckItem(
                    fact="사실 B",
                    supporting_section=dataclasses.replace(original),
                    polarity="negative",
                ),
                FactCheckItem(
                    fact="사실 C", supporting_section=another_section, polarity="neutral"
                ),
            ],
        )
        at = _run(monkeypatch, card.ReportCard(fact_check=result, phrase_stats=None))
        sources = [e for e in at.expander if e.label.startswith("근거 ")]
        assert not at.exception
        assert len(sources) == 2
        for column in at.columns:
            assert any("[근거 1](#source-1)" in c.value for c in column.caption)
        assert any("다른 근거 본문" in m.value for m in sources[1].markdown)

    def test_failed_lookup_keeps_original_query_after_editing_draft(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        at = _run(monkeypatch, TimeoutError("timeout"), text="실패한 원래 조회 문장입니다")
        next(b for b in at.button if b.label == "다시 조회하기").click().run()
        at.text_area[0].input("새로 작성 중인 다른 문장입니다").run()
        next(b for b in at.button if b.label == "직전 결과 보기").click().run()
        assert not at.exception
        assert any("실패한 원래 조회 문장입니다" in t.value for t in at.text)
        assert not any("LLM API" in e.value for e in at.error)

    def test_result_sections_have_level_two_headings(self, monkeypatch: pytest.MonkeyPatch) -> None:
        at = _run(
            monkeypatch,
            card.ReportCard(fact_check=FactCheckResult(claim="루머", items=[]), phrase_stats=None),
        )
        assert not at.exception
        assert {h.value for h in at.header} == {"공시 사실 대조", "🔍 이 표현이 가리키는 맥락"}

    def test_lookup_starts_on_result_page_and_does_not_repeat_on_rerun(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        observed_routes = []

        def build(*args, **kwargs):
            ctx = get_script_run_ctx()
            assert ctx is not None
            pages = ctx.pages_manager.get_pages()
            observed_routes.append(
                pages[ctx.pages_manager.current_page_script_hash]["url_pathname"]
            )
            return card.ReportCard(
                fact_check=FactCheckResult(claim="조회 문장", items=[]), phrase_stats=None
            )

        monkeypatch.setattr(card, "build_report", build)
        at = _app().run()
        at.selectbox[0].select("삼성전자").run()
        at.text_area[0].input("유상증자 공시를 확인합니다.").run()
        _confirm_button(at).click().run()
        assert not at.exception
        assert observed_routes == ["result"]
        at.run()
        assert observed_routes == ["result"]


class TestPrinterOutput:
    @staticmethod
    def _has_feed(at: AppTest) -> bool:
        return any("animation: paperFeed" in element.proto.body for element in at.get("html"))

    def test_new_result_feeds_once_and_next_lookup_feeds_again(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result = card.ReportCard(
            fact_check=FactCheckResult(claim="공시 조회", items=[]), phrase_stats=None
        )
        at = _run(monkeypatch, result)
        assert not at.exception
        assert self._has_feed(at)
        at.run()
        assert not self._has_feed(at)
        assert any("관련 공시를 찾지 못했습니다" in i.value for i in at.info)

        next(b for b in at.button if b.label == "다시 조회하기").click().run()
        at.text_area[0].input("다음 조회의 출력 연출을 확인합니다.").run()
        _confirm_button(at).click().run()
        assert not at.exception
        assert self._has_feed(at)

    def test_reopening_saved_result_does_not_replay_feed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result = card.ReportCard(
            fact_check=FactCheckResult(claim="공시 조회", items=[]), phrase_stats=None
        )
        at = _run(monkeypatch, result)
        assert self._has_feed(at)
        next(b for b in at.button if b.label == "다시 조회하기").click().run()
        next(b for b in at.button if b.label == "직전 결과 보기").click().run()
        assert not at.exception
        assert not self._has_feed(at)
        assert any("관련 공시를 찾지 못했습니다" in i.value for i in at.info)

    def test_error_is_immediate_and_successful_retry_feeds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        at = _run(monkeypatch, TimeoutError("test timeout"))
        assert not at.exception
        assert at.error
        assert not self._has_feed(at)

        monkeypatch.setattr(
            card,
            "build_report",
            lambda *a, **k: card.ReportCard(
                fact_check=FactCheckResult(claim="공시 조회", items=[]), phrase_stats=None
            ),
        )
        next(b for b in at.button if b.label == "다시 조회하기").click().run()
        _confirm_button(at).click().run()
        assert not at.exception
        assert not at.error
        assert self._has_feed(at)


class TestLookupHistory:
    @staticmethod
    def _open(at: AppTest) -> AppTest:
        # AppTest 1.63의 Tab에는 select()가 없다. 공개된 keyed tabs 상태로 선택한다.
        at.session_state["_workspace_tab"] = "조회 기록"
        return at.run()

    @staticmethod
    def _card(fact: str) -> card.ReportCard:
        return card.ReportCard(
            fact_check=FactCheckResult(
                claim="조회 문장",
                items=[
                    FactCheckItem(fact=fact, supporting_section=_section(fact), polarity="positive")
                ],
            ),
            phrase_stats=None,
        )

    def test_history_tab_follows_glossary_and_explains_empty_state(self) -> None:
        at = self._open(_app(powered=False).run())
        assert not at.exception
        assert [tab.label for tab in at.tabs] == ["팩트체크·화법통계", "사전", "조회 기록"]
        assert any("아직 보관된 조회 결과가 없습니다" in i.value for i in at.tabs[2].info)

    def test_new_session_can_choose_older_document_without_requery(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        at = _run(monkeypatch, self._card("첫 번째에 확인한 고유 사실"), text="첫 조회 문장입니다")
        next(b for b in at.button if b.label == "다시 조회하기").click().run()
        at.text_area[0].input("두 번째 조회 문장입니다").run()
        monkeypatch.setattr(card, "build_report", lambda *a, **k: self._card("두 번째 고유 사실"))
        _confirm_button(at).click().run()

        def unexpected_lookup(*args, **kwargs):
            raise AssertionError("보관한 결과를 고르는 동안 재조회하면 안 됩니다")

        monkeypatch.setattr(card, "build_report", unexpected_lookup)
        monkeypatch.setattr(app_module.PhraseClusterIndex, "match", unexpected_lookup)
        fresh = self._open(_app().run())
        assert not fresh.exception
        assert len(fresh.tabs) == 3
        history = fresh.tabs[2]
        assert any("두 번째 고유 사실" in m.value for m in history.markdown)
        assert not any("첫 번째에 확인한 고유 사실" in m.value for m in history.markdown)
        from disclosure_lit.demo.history import get_history

        older_id = get_history().entries()[-1].record_id
        history.radio[0].set_value(older_id)
        self._open(fresh)  # AppTest는 keyed tabs의 활성 탭을 다음 실행에 직렬화하지 않는다.
        assert not fresh.exception
        assert any("첫 번째에 확인한 고유 사실" in m.value for m in fresh.tabs[2].markdown)
        assert any("첫 조회 문장입니다" == t.value for t in fresh.tabs[2].text)
        assert not TestPrinterOutput._has_feed(fresh)

    def test_history_selection_does_not_overwrite_draft_and_errors_do_not_erase_history(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        at = _run(monkeypatch, self._card("보관해야 할 사실"))
        next(b for b in at.button if b.label == "다시 조회하기").click().run()
        at.text_area[0].input("아직 제출하지 않은 다른 문장입니다").run()
        self._open(at)
        assert len(at.tabs) == 3
        assert any("보관해야 할 사실" in m.value for m in at.tabs[2].markdown)
        assert at.text_area[0].value == "아직 제출하지 않은 다른 문장입니다"
        at.session_state["_workspace_tab"] = "팩트체크·화법통계"
        at.run()

        def fail(*args, **kwargs):
            raise TimeoutError("의도한 실패")

        monkeypatch.setattr(card, "build_report", fail)
        _confirm_button(at).click().run()
        assert at.error
        next(b for b in at.button if b.label == "다시 조회하기").click().run()
        self._open(at)
        assert not at.exception
        assert len(at.tabs[2].radio[0].options) == 1
        assert any("보관해야 할 사실" in m.value for m in at.tabs[2].markdown)

    def test_saved_graph_scope_and_evicted_selection_survive_new_session(self) -> None:
        from disclosure_lit.demo.history import (
            HISTORY_LIMIT,
            LookupResult,
            MeaningSnapshot,
            get_history,
        )

        history = get_history()
        result = LookupResult(
            text="조회 당시의 원문입니다",
            corp_code="00126380",
            corp_name="삼성전자",
            card=self._card("저장된 맥락과 함께 읽을 사실"),
            is_sample=False,
            meaning=MeaningSnapshot(
                graph_dot='digraph { a [label="보관한 맥락"] }', scope="2026년"
            ),
        )
        evicted = history.add(result)
        for _ in range(HISTORY_LIMIT):
            latest = history.add(result)
        at = _app(powered=False).run()
        at.session_state["_history_selection"] = evicted.record_id
        self._open(at)
        assert not at.exception
        assert len(at.tabs[2].radio[0].options) == HISTORY_LIMIT
        assert at.tabs[2].radio[0].value == latest.record_id
        assert "보관한 맥락" in at.tabs[2].get("graphviz_chart")[0].proto.spec
        assert any("조회 당시 수집된 코퍼스(2026년)" in c.value for c in at.tabs[2].caption)


class TestFactContextBadges:
    """공시 유형·날짜·확정 여부·재공시 예정 — fact 문장만으로는 안 드러나는 맥락을
    caption으로 함께 보여준다(2026-09-21, 사용자 피드백: "더 유의미한 답변")."""

    def test_form_type_and_date_badge_rendered(self, monkeypatch: pytest.MonkeyPatch) -> None:
        section = DisclosureSection(
            rcept_no="R1",
            corp_code="00126380",
            form_type="유상증자",
            section_title="제목",
            text="본문",
            rcept_dt="20260810",
        )
        fact_check = FactCheckResult(
            claim="유상증자 루머",
            items=[
                FactCheckItem(fact="뒷받침 사실", supporting_section=section, polarity="positive")
            ],
        )
        card_result = card.ReportCard(fact_check=fact_check, phrase_stats=None)
        at = _run(monkeypatch, card_result, text="삼성전자 유상증자 루머")

        assert not at.exception
        caption_values = [c.value for c in at.caption]
        assert any("유상증자" in c and "2026-08-10" in c for c in caption_values)

    def test_unconfirmed_fact_shows_review_badge_not_confirmed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fact_check = FactCheckResult(
            claim="매각설",
            items=[
                FactCheckItem(
                    fact="검토 중",
                    supporting_section=_section("검토 중"),
                    polarity="neutral",
                    is_confirmed=False,
                )
            ],
        )
        card_result = card.ReportCard(fact_check=fact_check, phrase_stats=None)
        at = _run(monkeypatch, card_result, text="삼성전자 매각설")

        assert not at.exception
        caption_values = [c.value for c in at.caption]
        assert any("미확정" in c for c in caption_values)
        assert not any("✅ 확정" in c for c in caption_values)

    def test_confirmed_fact_shows_no_confirmed_badge(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """확정이 기본값(대다수)이라 매번 표시하면 노이즈만 늘어난다 — 예외 상태
        (미확정)일 때만 배지를 보여주고, 확정 상태는 아무 배지도 안 붙인다."""
        fact_check = FactCheckResult(
            claim="유상증자 루머",
            items=[
                FactCheckItem(
                    fact="뒷받침 사실",
                    supporting_section=_section("근거1"),
                    polarity="positive",
                    is_confirmed=True,
                )
            ],
        )
        card_result = card.ReportCard(fact_check=fact_check, phrase_stats=None)
        at = _run(monkeypatch, card_result, text="삼성전자 유상증자 루머")

        assert not at.exception
        assert not any("확정" in c.value for c in at.caption)

    def test_next_update_note_rendered_when_present(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fact_check = FactCheckResult(
            claim="매각설",
            items=[
                FactCheckItem(
                    fact="검토 중",
                    supporting_section=_section("검토 중"),
                    polarity="neutral",
                    is_confirmed=False,
                    next_update_note="3개월 이내(2026-12-08) 재공시 예정",
                )
            ],
        )
        card_result = card.ReportCard(fact_check=fact_check, phrase_stats=None)
        at = _run(monkeypatch, card_result, text="삼성전자 매각설")

        assert not at.exception
        assert any("3개월 이내(2026-12-08) 재공시 예정" in c.value for c in at.caption)

    def test_no_next_update_note_when_absent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fact_check = FactCheckResult(
            claim="유상증자 루머",
            items=[
                FactCheckItem(
                    fact="뒷받침 사실", supporting_section=_section("근거1"), polarity="positive"
                )
            ],
        )
        card_result = card.ReportCard(fact_check=fact_check, phrase_stats=None)
        at = _run(monkeypatch, card_result, text="삼성전자 유상증자 루머")

        assert not at.exception
        assert not any("다음 업데이트" in c.value for c in at.caption)

    def test_reviewed_disclosure_count_summary(self, monkeypatch: pytest.MonkeyPatch) -> None:
        section_a = DisclosureSection(
            rcept_no="R1",
            corp_code="00126380",
            form_type="유상증자",
            section_title="제목",
            text="본문",
        )
        section_b = DisclosureSection(
            rcept_no="R2",
            corp_code="00126380",
            form_type="유상증자",
            section_title="제목",
            text="본문",
        )
        fact_check = FactCheckResult(
            claim="유상증자 루머",
            items=[
                FactCheckItem(fact="사실1", supporting_section=section_a, polarity="positive"),
                FactCheckItem(fact="사실2", supporting_section=section_a, polarity="neutral"),
                FactCheckItem(fact="사실3", supporting_section=section_b, polarity="negative"),
            ],
        )
        card_result = card.ReportCard(fact_check=fact_check, phrase_stats=None)
        at = _run(monkeypatch, card_result, text="삼성전자 유상증자 루머")

        assert not at.exception
        # 서로 다른 rcept_no 2건 — 같은 공시를 근거로 든 사실이 여러 개여도 중복 집계 안 함.
        assert any("근거로 채택된 공시 2건" in c.value for c in at.caption)


class TestUnexpectedPolarity:
    """리뷰 지적(@powercodingman): polarity가 positive/negative/neutral 밖이면
    setdefault로 새 버킷만 만들고 어느 컬럼에도 안 그려서 조용히 사라졌었다."""

    def test_unknown_polarity_is_shown_not_silently_dropped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fact_check = FactCheckResult(
            claim="루머",
            items=[
                FactCheckItem(fact="분류 애매한 사실", supporting_section=None, polarity="mixed"),
            ],
        )
        card_result = card.ReportCard(fact_check=fact_check, phrase_stats=None)
        at = _run(monkeypatch, card_result)

        assert not at.exception
        # 2026-09-22 UX 점검: 예전엔 "polarity 값이 ... verify_claim() 확인 필요"라는
        # 내부 함수명이 그대로 노출됐다. 사용자에게는 "분류되지 않았지만 버리지 않았다"만
        # 보이면 되고, 진단용 원본 값은 로그로 돌렸다.
        assert not any("verify_claim" in w.value for w in at.warning)
        assert any("분류되지 않은 사실" in h.value for h in at.subheader)
        assert any("분류 애매한 사실" in m.value for m in at.markdown)


class TestSampleFallback:
    """B의 엔진이 아직 NotImplementedError일 때 — 레이아웃 확인용 샘플 카드.

    샘플도 "실제로 시도됐을 경로"에 맞춰 골라야 한다(PR #33 리뷰 지적,
    @powercodingman) — 종목 미특정 입력에 팩트체크 카드를 같이 그리면 엔진이 붙은
    뒤엔 절대 안 나올 화면을 보여주게 된다.
    """

    def test_phrase_only_sample_unreachable_once_company_is_selected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`extract_claim`이 스텁이라 텍스트에서 종목을 못 잡아도(2026-09-11 회귀와
        같은 이유로 monkeypatch), 2026-09-18 종목 선택 드롭다운이 필수화된 뒤로는
        `_build_card`가 그 드롭다운 corp_code를 그대로 받으므로 "종목 미특정" 경로
        자체가 `main()`에서는 더 이상 안 나온다 — `_run()`이 항상 회사를 하나
        선택하기 때문이다. 팩트체크 샘플이 뜨는 게 맞는 동작이다."""

        def raiser(text: str):
            raise NotImplementedError

        monkeypatch.setattr(card, "extract_claim", raiser)
        at = _run(monkeypatch, NotImplementedError(), text="선제적 재무구조 개선")

        assert not at.exception
        assert any("🧪" in c.value for c in at.caption)
        assert {s.value for s in at.header} == {"공시 사실 대조"}

    def test_company_specified_input_shows_factcheck_section(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`extract_claim`이 구현돼 종목을 잡아내면 팩트체크 샘플이 뜬다."""
        monkeypatch.setattr(
            card,
            "extract_claim",
            lambda text: Claim(
                corp_name="삼성전자", corp_code=None, keyword="유상증자", raw_text=text
            ),
        )
        at = _run(monkeypatch, NotImplementedError(), text="삼성전자 유상증자 루머")

        assert not at.exception
        assert any("🧪" in c.value for c in at.caption)
        assert {s.value for s in at.header} == {"공시 사실 대조"}

    def test_demo_toggle_forces_combined_sample(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """데모 전용 토글 — `extract_claim`이 스텁이라도 결합 화면을 볼 수 있어야 한다.

        추가 리뷰 지적(PR #33, @powercodingman): `resolve_claim` 폴백이 항상 종목
        미특정을 돌려주므로, 토글이 없으면 B의 엔진이 붙기 전까지 "종목 특정됨" 화면을
        팀에 보여줄 방법이 자체적으로 없다. 종목명 추측 휴리스틱을 `resolve_claim`에
        넣는 대신 데모 쪽 명시적 스위치로 해결한 것을 여기서 못박는다.
        """

        def raiser(*args: object, **kwargs: object) -> None:
            raise NotImplementedError

        monkeypatch.setattr(card, "build_report", raiser)
        # extract_claim(B 담당)이 실구현된 뒤로는 resolve_claim이 그걸 그대로 불러
        # 진짜 LLM을 호출한다 — force_corp_sample 토글 분기라 결과 자체엔 영향 없지만
        # 느려지고 API 비용이 드니 여기서도 스텁 상태를 흉내낸다(위 두 테스트와 동일
        # 회귀, 2026-09-11).
        monkeypatch.setattr(card, "extract_claim", raiser)

        at = _app()
        at.run()
        at.checkbox[0].check().run()
        at.selectbox[0].select(next(iter(_TEST_CORP_NAMES.values()))).run()
        at.text_area[0].input("선제적 재무구조 개선").run()
        _confirm_button(at).click().run()

        assert not at.exception
        # 같은 입력을 토글 없이 넣으면 아무 subheader도 안 나온다
        # (test_phrase_only_input_shows_only_phrase_sample) — 토글이 실제로 분기를
        # 바꿔 팩트체크 샘플을 붙였는지 대조해 확인한다.
        assert {s.value for s in at.header} == {"공시 사실 대조"}

    def test_toggle_is_off_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """기본값은 꺼짐 — 아무도 안 켰는데 켠 것과 똑같이 동작하면 안 된다.

        2026-09-18: 종목 선택 드롭다운이 필수화된 뒤로는 팩트체크 샘플이 토글과
        무관하게(드롭다운이 corp_code를 항상 주므로) 뜬다 — 그래서 여기서는 화면
        내용이 아니라 체크박스 자체의 기본값만 확인한다(진짜 회귀 대상)."""

        def raiser(text: str):
            raise NotImplementedError

        monkeypatch.setattr(card, "extract_claim", raiser)
        at = _run(monkeypatch, NotImplementedError(), text="선제적 재무구조 개선")

        assert at.checkbox[0].value is False


class TestCompanySelector:
    """2026-09-18 추가(사용자 요청) — 종목 선택 드롭다운. 이 서비스는 항상 회사
    하나를 특정해 조회하므로, `extract_claim`(LLM)의 회사명 추출·exact-match 이름
    조회에 기대지 않고 사용자가 목록에서 직접 골라 corp_code를 확정한다."""

    def test_no_company_options_shows_warning_and_stops(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`_corp_names()`가 빈 dict면(코퍼스 없음) 드롭다운·입력창 없이 경고만
        보여주고 끝나야 한다 — 선택지 없는 드롭다운을 그려봐야 아무도 못 고른다."""
        monkeypatch.setattr(candidates_module, "load_corpus_companies", lambda _raw_dir: {})

        at = _app()
        at.run()

        assert not at.exception
        assert any("불러오지 못했습니다" in w.value for w in at.warning)
        assert at.selectbox == []
        assert at.text_area == []

    def test_confirm_button_disabled_until_company_selected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """2026-09-22 UX 점검: 종목을 고르기 전에는 확인 버튼을 누를 수 없다.

        예전엔 누를 수 있게 해두고 누른 뒤에야 "종목을 선택해주세요"라고 알려줬다 —
        헛클릭을 한 번 시킨 뒤 알려주는 것보다, 왜 못 누르는지 미리 보여주는 게 낫다.
        조회가 실제로 진행되지 않는다는 보장은 그대로 유지한다."""

        def fail_build_report(*args: object, **kwargs: object) -> None:
            raise AssertionError("종목 미선택 상태로 build_report가 호출되면 안 된다")

        monkeypatch.setattr(card, "build_report", fail_build_report)

        at = _app()
        at.run()
        at.text_area[0].input("아무 문장").run()

        assert not at.exception
        assert _confirm_button(at).disabled is True

        # 종목을 고르면 그때 눌리고, 조회도 실제로 진행된다
        at.selectbox[0].select(next(iter(_TEST_CORP_NAMES.values()))).run()
        assert _confirm_button(at).disabled is False

    def test_selected_company_code_passed_as_override(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """드롭다운에서 고른 회사의 corp_code가 `build_report(corp_code_override=...)`
        로 그대로 전달돼야 한다 — LLM 회사명 추출/이름 매칭을 거치지 않는다는 게 이
        기능의 핵심이므로, 실제로 override 인자가 넘어가는지 직접 확인한다."""
        received: dict[str, object] = {}

        def fake_build_report(text: str, **kwargs: object) -> card.ReportCard:
            received.update(kwargs)
            return card.ReportCard(fact_check=None, phrase_stats=None)

        monkeypatch.setattr(card, "build_report", fake_build_report)

        at = _app()
        at.run()
        at.selectbox[0].select("삼성전자").run()
        at.text_area[0].input("충분히 긴 문장을 입력합니다").run()
        _confirm_button(at).click().run()

        assert not at.exception
        assert received["corp_code_override"] == "00126380"


class TestCompanyQueryHint:
    """2026-09-18 추가(사용자 요청 — "종목 선택 직후 컨텍스트 요약") — 종목을
    고르자마자 그 회사에 뭘 물어볼 수 있는지 힌트를 준다."""

    def test_selecting_company_does_not_load_full_corpus_or_run_lookup(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """선택은 안내만 갱신한다. 전체 코퍼스 읽기와 실제 조회는 확인 버튼의 몫이다."""
        requested_paths = []

        def load_selected(path: Path) -> list[DisclosureSection]:
            requested_paths.append(path)
            return [_section()]

        def unexpected_work(*args, **kwargs):
            raise AssertionError("종목 선택만으로 전체 코퍼스나 조회를 실행하면 안 됩니다")

        monkeypatch.setattr(candidates_module, "load_sections_jsonl", load_selected)
        monkeypatch.setattr(candidates_module, "load_all_local_sections", unexpected_work)
        monkeypatch.setattr(card, "build_report", unexpected_work)
        at = _app().run()
        at.selectbox[0].select("삼성전자").run()

        assert not at.exception
        assert any("공시 1건" in caption.value for caption in at.caption)
        assert requested_paths == [app_module._RAW_DIR / "00126380" / "sections.jsonl"]

        # 문장 편집으로 재실행돼도 회사 파일을 다시 읽지 않는다.
        at.text_area[0].input("유상증자를 검토 중이라는 문장입니다.").run()
        assert not at.exception
        assert len(requested_paths) == 1

    def test_returns_none_when_no_sections_for_corp(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(app_module, "load_sections_jsonl", lambda _path: [])
        assert _company_query_hint("00126380") is None

    def test_summarizes_count_period_and_top_types(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sections = [
            DisclosureSection(
                rcept_no="R1",
                corp_code="00126380",
                form_type="유상증자",
                section_title="",
                text="",
                rcept_dt="20260301",
            ),
            DisclosureSection(
                rcept_no="R2",
                corp_code="00126380",
                form_type="유상증자",
                section_title="",
                text="",
                rcept_dt="20260415",
            ),
            DisclosureSection(
                rcept_no="R3",
                corp_code="00126380",
                form_type="MOU",
                section_title="",
                text="",
                rcept_dt="20260201",
            ),
            # 다른 회사 — 집계에서 빠져야 한다.
            DisclosureSection(
                rcept_no="R4",
                corp_code="99999999",
                form_type="MOU",
                section_title="",
                text="",
                rcept_dt="20260101",
            ),
        ]
        monkeypatch.setattr(app_module, "load_sections_jsonl", lambda _path: sections)

        hint = _company_query_hint("00126380")

        assert hint is not None
        assert "3건" in hint
        assert "2026-02~2026-04" in hint
        assert "유상증자(2건)" in hint

    def test_missing_dates_omit_period(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sections = [
            DisclosureSection(
                rcept_no="R1",
                corp_code="00126380",
                form_type="MOU",
                section_title="",
                text="",
                rcept_dt="",
            ),
        ]
        monkeypatch.setattr(app_module, "load_sections_jsonl", lambda _path: sections)

        hint = _company_query_hint("00126380")

        assert hint is not None
        assert "1건" in hint
        assert "~" not in hint


class TestOnboardingAndExampleChips:
    """2026-09-18 추가(사용자 요청 — "전체적으로 사용자 친화적이지 않고 정확한 질문을
    이끌어낼 요소가 부족") — 온보딩 설명, 예시 칩, 최소 길이 검증."""

    def test_onboarding_expander_present(self) -> None:
        at = _app()
        at.run()

        assert not at.exception
        assert any("무엇을 보여드리나요" in e.label for e in at.expander)

    def test_example_chip_fills_text_area(self) -> None:
        """2026-09-22: `st.columns(3)` + 버튼에서 `st.pills`로 바꿨다 — 고정 3분할은
        좁은 화면에서 긴 예시 문장이 뭉개지는데 pills는 폭에 맞춰 줄바꿈한다."""
        at = _app()
        at.run()
        at.pills[0].set_value(_EXAMPLE_QUERIES[0]).run()

        assert not at.exception
        assert at.text_area[0].value == _EXAMPLE_QUERIES[0]

    def test_example_chip_does_not_overwrite_user_edits(self) -> None:
        """칩을 고른 뒤 그 위에 직접 고쳐 쓰면, 이후 재실행에서 칩 값이 다시 덮어쓰지
        않아야 한다 — pills는 선택 상태가 유지되므로 매 재실행마다 반영하면 사용자가
        쓴 내용이 계속 날아간다."""
        at = _app()
        at.run()
        at.pills[0].set_value(_EXAMPLE_QUERIES[0]).run()
        at.text_area[0].input("직접 고쳐 쓴 문장입니다").run()

        assert not at.exception
        assert at.text_area[0].value == "직접 고쳐 쓴 문장입니다"

    def test_reselecting_same_chip_after_deselect_refills_text_area(self) -> None:
        """PR #113 리뷰(@9abin) 회귀 — 예시 A 선택 → 직접 수정 → A 선택 해제 → A 재선택
        순서에서 입력창이 A로 돌아와야 한다. 예전엔 해제(None) 때 "마지막으로 적용한 칩"
        기록을 갱신하지 않아, 재선택을 "이미 적용한 값"으로 보고 무시했다."""
        at = _app()
        at.run()
        at.pills[0].set_value(_EXAMPLE_QUERIES[0]).run()
        at.text_area[0].input("직접 고쳐 쓴 문장입니다").run()
        _clear_pills(at)
        assert at.pills[0].value is None  # 해제가 실제로 전달됐는지부터 확인
        at.pills[0].set_value(_EXAMPLE_QUERIES[0]).run()

        assert not at.exception
        assert at.text_area[0].value == _EXAMPLE_QUERIES[0]

    def test_short_query_warns_and_does_not_call_build_report(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fail_build_report(*args: object, **kwargs: object) -> None:
            raise AssertionError("너무 짧은 입력으로 build_report가 호출되면 안 된다")

        monkeypatch.setattr(card, "build_report", fail_build_report)

        at = _app()
        at.run()
        at.selectbox[0].select(next(iter(_TEST_CORP_NAMES.values()))).run()
        at.text_area[0].input("짧음").run()
        _confirm_button(at).click().run()

        assert not at.exception
        assert len("짧음") < _MIN_QUERY_LENGTH
        assert any("너무 짧습니다" in w.value for w in at.warning)

    def test_query_at_min_length_proceeds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """경계값 — 정확히 최소 길이면 통과해야 한다(그보다 짧을 때만 막는다)."""
        called = False

        def fake_build_report(*args: object, **kwargs: object) -> card.ReportCard:
            nonlocal called
            called = True
            return card.ReportCard(fact_check=None, phrase_stats=None)

        monkeypatch.setattr(card, "build_report", fake_build_report)
        boundary_text = "가" * _MIN_QUERY_LENGTH

        at = _app()
        at.run()
        at.selectbox[0].select(next(iter(_TEST_CORP_NAMES.values()))).run()
        at.text_area[0].input(boundary_text).run()
        _confirm_button(at).click().run()

        assert not at.exception
        assert called


class TestGlossaryTab:
    """이슈 #95(3안-A) — 사전 탭이 GLOSSARY를 전부 렌더링하는지, 팩트체크 결과와
    무관하게(버튼을 안 눌러도) 항상 뜨는지 확인한다."""

    def test_glossary_tab_lists_all_terms_before_any_interaction(self) -> None:
        """사전 탭은 팩트체크 입력·버튼 클릭과 무관한 정적 콘텐츠라, `at.run()` 직후
        (아무 상호작용 전)에도 이미 다 떠 있어야 한다."""
        at = _app()
        at.run()

        assert not at.exception
        assert len(at.tabs) == 3
        glossary_tab = at.tabs[1]
        markdown_values = [m.value for m in glossary_tab.markdown]
        assert len(markdown_values) == len(GLOSSARY)
        for term in GLOSSARY:
            assert any(f"**{term}**" in m for m in markdown_values)

    def test_glossary_tab_shows_source_for_each_term(self) -> None:
        at = _app()
        at.run()

        glossary_tab = at.tabs[1]
        caption_values = [c.value for c in glossary_tab.caption]
        source_captions = [c for c in caption_values if c.startswith("출처:")]
        assert len(source_captions) == len(GLOSSARY)


class TestCorpusScopeSummary:
    """과적합/일반화 우려에 대한 UI 투명성 문구(회사 단위 중복 제거 PR #64 후속 논의) —
    `_render_phrase_stats()`가 보여주는 통계 옆에 이 코퍼스의 범위(회사 수·공시 시기)를
    명시해, 표본이 이 범위 밖에도 같은 비율로 일반화된다는 보장이 없음을 드러낸다."""

    @pytest.fixture(autouse=True)
    def _clear_cache_resource(self) -> None:
        """`_corpus_scope_summary()`는 `@st.cache_resource`라, 같은 함수 객체를 여러
        테스트에서 직접 import해 호출하면(AppTest처럼 매번 새 모듈을 다시 exec하는 게
        아니라서) 첫 호출 결과가 캐시돼 이후 monkeypatch가 무시된다 — 매 테스트 전에
        캐시를 비운다."""
        app_module._corpus_scope_summary.clear()

    def test_empty_corpus_returns_empty_string(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(app_module, "_local_sections", lambda: [])

        assert _corpus_scope_summary() == ""

    def test_counts_distinct_companies_and_date_range(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sections = [
            DisclosureSection(
                rcept_no="R1",
                corp_code="C1",
                form_type="MOU",
                section_title="",
                text="",
                rcept_dt="20260301",
            ),
            DisclosureSection(
                rcept_no="R2",
                corp_code="C1",
                form_type="MOU",
                section_title="",
                text="",
                rcept_dt="20260415",
            ),
            DisclosureSection(
                rcept_no="R3",
                corp_code="C2",
                form_type="MOU",
                section_title="",
                text="",
                rcept_dt="20260901",
            ),
        ]
        monkeypatch.setattr(app_module, "_local_sections", lambda: sections)

        summary = _corpus_scope_summary()

        assert "2개사" in summary  # C1, C2 — R1/R2가 같은 회사라 중복 집계 안 됨
        assert "2026-03~2026-09" in summary

    def test_missing_rcept_dt_falls_back_to_unknown_period(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sections = [
            DisclosureSection(
                rcept_no="R1", corp_code="C1", form_type="MOU", section_title="", text=""
            )
        ]
        monkeypatch.setattr(app_module, "_local_sections", lambda: sections)

        summary = _corpus_scope_summary()

        assert "1개사" in summary
        assert "시기 미상" in summary


class TestCorpNames:
    """2026-09-18 추가 — `_render_meaning_map()`이 corp_code만 갖고 있을 때(LLM을
    다시 안 부르고) 사람이 읽을 회사명으로 보여주는 용도."""

    @pytest.fixture(autouse=True)
    def _clear_cache_resource(self) -> None:
        app_module._corp_names.clear()

    def test_missing_file_returns_empty_dict(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(app_module, "_RAW_DIR", tmp_path)

        assert app_module._corp_names() == {}

    def test_loads_corp_code_to_name_mapping(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(app_module, "_RAW_DIR", tmp_path)
        (tmp_path / "corpus_companies.json").write_text(
            json.dumps(
                [
                    {"corp_code": "00126380", "corp_name": "삼성전자"},
                    {"corp_code": "00164779", "corp_name": "SK하이닉스"},
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        names = app_module._corp_names()

        assert names == {"00126380": "삼성전자", "00164779": "SK하이닉스"}

    def test_corrupted_file_returns_empty_dict_without_crashing(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(app_module, "_RAW_DIR", tmp_path)
        (tmp_path / "corpus_companies.json").write_text("이건 json이 아님", encoding="utf-8")

        assert app_module._corp_names() == {}


class TestCorpCodeIndex:
    """종목명(corp_name)만으로 corp_code를 찾는 경로 — 예전엔 빈 CorpCodeIndex([])라
    "삼성전자 유상증자설"처럼 종목명으로만 된 루머(데모의 핵심 시나리오)를 한 번도
    해석 못 했다. DartClient.fetch_corp_codes()를 실제로 연결했는지 확인한다."""

    @pytest.fixture(autouse=True)
    def _clear_cache_resource(self) -> None:
        """`_corp_code_index()`도 `@st.cache_resource`라 TestCorpusScopeSummary와
        같은 이유로 매 테스트 전에 캐시를 비워야 monkeypatch가 먹는다."""
        app_module._corp_code_index.clear()

    def test_no_api_key_falls_back_to_empty_index_without_crashing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(app_module, "settings", dataclasses.replace(settings, dart_api_key=""))

        index = _corp_code_index()

        assert index.find_by_name("삼성전자") is None

    def test_fetch_failure_falls_back_to_empty_index_without_crashing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """DART_API_KEY는 있어도 네트워크 오류 등으로 fetch_corp_codes가 실패할 수
        있다 — 종목명 해석만 못 할 뿐 데모 전체가 죽으면 안 된다."""
        monkeypatch.setattr(
            app_module, "settings", dataclasses.replace(settings, dart_api_key="dummy-key")
        )

        class _RaisingClient:
            def __init__(self, api_key: str) -> None:
                pass

            def fetch_corp_codes(self, *, cache_path: Path) -> list[CorpCodeEntry]:
                raise RuntimeError("network down")

        monkeypatch.setattr(app_module, "DartClient", _RaisingClient)

        index = _corp_code_index()

        assert index.find_by_name("삼성전자") is None

    def test_successful_fetch_populates_index_for_name_lookup(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            app_module, "settings", dataclasses.replace(settings, dart_api_key="dummy-key")
        )
        entries = [
            CorpCodeEntry(
                corp_code="00126380",
                corp_name="삼성전자",
                stock_code="005930",
                modify_date="20260101",
            )
        ]

        class _FakeClient:
            def __init__(self, api_key: str) -> None:
                pass

            def fetch_corp_codes(self, *, cache_path: Path) -> list[CorpCodeEntry]:
                return entries

        monkeypatch.setattr(app_module, "DartClient", _FakeClient)

        index = _corp_code_index()

        entry = index.find_by_name("삼성전자")
        assert entry is not None
        assert entry.corp_code == "00126380"


class TestMatchClusterForText:
    """실측(2026-09-18, 사용자 제보 — 삼성전자 공정거래법 제26조 대규모내부거래
    공시)에서 확인된 문제: `_render_meaning_map()`이 팩트체크 근거 원문 "전체"를
    그대로 한 번만 매칭했는데, 표 양식 공시는 항목 라벨·숫자 필드가 섞여 있어
    전체 임베딩이 상투구 centroid와 거의 안 맞는다(실측 최고 유사도 0.47, 문턱
    0.75). `_build_outcome_labels()`가 코퍼스 빌드 시점에 쓰는 "섹션 -> 후보 추출
    -> 일괄 매칭" 패턴을 질의 시점에도 재사용하는 폴백을 검증한다."""

    @staticmethod
    def _reliable(cluster_id: str, phrase: str) -> ClusterMeaning:
        return ClusterMeaning(
            cluster_id=cluster_id,
            representative_phrase=phrase,
            dominant_form_type="기타",
            purity=1.0,
            occurrence_count=10,
            company_count=10,
            is_reliable=True,
        )

    def test_whole_text_match_short_circuits_candidate_extraction(self) -> None:
        """전체 텍스트가 신뢰 가능한 클러스터에 바로 매칭되면 후보 추출 없이 그
        결과를 그대로 쓴다(R104-1 수정 후: 신뢰 가능할 때만 짧은 경로를 탄다)."""
        text = "선제적 재무구조 개선을 추진하고 있습니다."
        index = _FakeIndex({}, whole_text_matches={text: "pc-whole"})
        meanings = {"pc-whole": self._reliable("pc-whole", text)}

        cluster = _match_cluster_for_text(index, text, meanings)  # type: ignore[arg-type]

        assert cluster is not None
        assert cluster.cluster_id == "pc-whole"
        assert index.match_many_calls == []  # 후보 추출/일괄 매칭까지 안 갔다

    def test_falls_back_to_narrative_candidate_inside_table_text(self) -> None:
        """전체 매칭 실패 시, 표 양식 텍스트 안에 묻힌 서술형 문장을 뽑아 재시도한다."""
        narrative = "이사회 의결을 통해 신규 투자를 결정하였습니다."
        table_text = f"1. 거래상대방 국가 AI컴퓨팅센터 출자금액 120. {narrative}"
        index = _FakeIndex({narrative: "pc-narrative"})  # whole_text_matches 없음 = 전체 매칭 실패

        cluster = _match_cluster_for_text(index, table_text, {})  # type: ignore[arg-type]

        assert cluster is not None
        assert cluster.cluster_id == "pc-narrative"

    def test_returns_none_when_no_narrative_candidate_extracted(self) -> None:
        """전체 매칭도 실패하고, 서술형 종결어미를 가진 후보 자체가 안 나오면
        (순수 표 필드 나열) None을 반환한다 — match_many를 아예 호출하지 않는다."""
        table_text = "출자금액 120 총출자액 4,000 참석 5 불참 0"
        index = _FakeIndex({})

        cluster = _match_cluster_for_text(index, table_text, {})  # type: ignore[arg-type]

        assert cluster is None
        assert index.match_many_calls == []

    def test_returns_none_when_candidates_extracted_but_none_match(self) -> None:
        table_text = "이건 충분히 긴 서술형 문장이지만 알려진 상투구는 아닙니다."
        index = _FakeIndex({})  # 어떤 후보도 매칭 안 됨

        cluster = _match_cluster_for_text(index, table_text, {})  # type: ignore[arg-type]

        assert cluster is None

    def test_picks_first_matching_candidate_when_several_are_extracted(self) -> None:
        """여러 서술형 후보 중 앞쪽 것이 매칭 안 되고 뒤쪽 것이 매칭되면, 그래도
        뒤쪽 매칭 결과를 찾아내야 한다(첫 번째만 보고 포기하면 안 됨) — 신뢰
        정보가 없을 때(빈 meanings)는 예전처럼 찾아낸 첫 매칭을 그대로 쓴다."""
        first = "이 문장은 알려진 상투구가 아닙니다."
        second = "선제적 재무구조 개선을 추진하고 있습니다."
        table_text = f"{first} {second}"
        index = _FakeIndex({second: "pc-second"})

        cluster = _match_cluster_for_text(index, table_text, {})  # type: ignore[arg-type]

        assert cluster is not None
        assert cluster.cluster_id == "pc-second"


class TestMatchClusterForTextPrefersReliableMeaning:
    """R104-1 (2026-09-21 외부 리뷰, 독립 재현으로 확인 — 실코퍼스 골드셋 기준 원시
    매칭 25/25 vs 표시 통과 8/25) — 점 2 수정(회사 수 조건을 인덱스 생성이 아니라
    ClusterMeaning.is_reliable로 분리)으로 신뢰 불가능한 클러스터도 인덱스에 남게
    됐는데, `_match_cluster_for_text()`는 첫 매칭만 반환하고 `is_reliable` 여부를
    전혀 모른다. 그 결과 `_render_meaning_map()`이 희귀(불신뢰) 클러스터에 먼저
    걸리면, 같은 입력 안에 신뢰 가능한 대안이 있어도 화면을 생략해버린다.

    아래 테스트는 수정 목표 인터페이스(`meanings` 인자를 받아 신뢰 가능한 후보를
    우선한다)를 정의한다 — 수정 전에는 실패한다(TypeError: 예상치 못한 인자)."""

    def test_prefers_reliable_cluster_over_earlier_unreliable_match(self) -> None:
        first = "이 문장은 회사 고유의 희귀한 표현입니다."
        second = "추후 구체적인 내용이 확정되는 시점에 재공시 하겠습니다."
        table_text = f"{first} {second}"
        index = _FakeIndex({first: "pc-rare", second: "pc-reliable"})
        meanings = {
            "pc-rare": ClusterMeaning(
                cluster_id="pc-rare",
                representative_phrase=first,
                dominant_form_type="기타",
                purity=1.0,
                occurrence_count=1,
                company_count=1,
                is_reliable=False,
            ),
            "pc-reliable": ClusterMeaning(
                cluster_id="pc-reliable",
                representative_phrase=second,
                dominant_form_type="조회공시",
                purity=0.95,
                occurrence_count=18,
                company_count=18,
                is_reliable=True,
            ),
        }

        cluster = _match_cluster_for_text(index, table_text, meanings)  # type: ignore[arg-type]

        assert cluster is not None
        assert cluster.cluster_id == "pc-reliable"

    def test_falls_back_to_first_match_when_no_candidate_is_reliable(self) -> None:
        """수용 기준: 표시 가능한 대안이 전혀 없으면 생략은 그대로 유지한다 — 즉
        전부 불신뢰일 때는 (호출부인 `_render_meaning_map()`이 그대로 생략할 수
        있도록) 예전처럼 첫 매칭을 반환한다."""
        text = "이 문장은 회사 고유의 희귀한 표현입니다."
        index = _FakeIndex({}, whole_text_matches={text: "pc-rare"})
        meanings = {
            "pc-rare": ClusterMeaning(
                cluster_id="pc-rare",
                representative_phrase=text,
                dominant_form_type="기타",
                purity=1.0,
                occurrence_count=1,
                company_count=1,
                is_reliable=False,
            ),
        }

        cluster = _match_cluster_for_text(index, text, meanings)  # type: ignore[arg-type]

        assert cluster is not None
        assert cluster.cluster_id == "pc-rare"

    def test_tries_candidates_when_whole_text_match_is_unreliable(self) -> None:
        """독립 code-reviewer 재검토 지적(MEDIUM) — 위 두 테스트는 "전체 텍스트
        자체가 불신뢰 클러스터로 매칭되는" 분기(app.py의 `whole is not None` 경로)를
        전혀 안 거친다(하나는 whole_text_matches가 아예 없고, 하나는 대안 자체가
        없다). R104-1이 고치려던 정확히 그 시나리오 — 전체 텍스트가 희귀 클러스터에
        먼저 매칭되더라도, 그 뒤에 뽑히는 문장 후보 중 신뢰 가능한 게 있으면 그걸
        써야 한다 — 를 직접 재현한다. `whole_meaning.is_reliable` 체크가 실수로
        빠지고 무조건 `return whole`로 되돌아가는 회귀가 생기면 이 테스트가 잡는다."""
        first = "이 문장은 회사 고유의 희귀한 표현입니다."
        second = "추후 구체적인 내용이 확정되는 시점에 재공시 하겠습니다."
        # 전체 텍스트(두 문장을 그대로 이어붙인 것) 자체가 먼저 "pc-rare"(불신뢰)로
        # 통째로 매칭되고, 그 뒤 문장 단위 후보 추출에서 두 번째 문장이 다시
        # "pc-reliable"으로 매칭되는 상황을 재현한다.
        whole_text = f"{first} {second}"
        index = _FakeIndex(
            {second: "pc-reliable"},
            whole_text_matches={whole_text: "pc-rare"},
        )
        meanings = {
            "pc-rare": ClusterMeaning(
                cluster_id="pc-rare",
                representative_phrase=whole_text,
                dominant_form_type="기타",
                purity=1.0,
                occurrence_count=1,
                company_count=1,
                is_reliable=False,
            ),
            "pc-reliable": ClusterMeaning(
                cluster_id="pc-reliable",
                representative_phrase=second,
                dominant_form_type="조회공시",
                purity=0.95,
                occurrence_count=18,
                company_count=18,
                is_reliable=True,
            ),
        }

        cluster = _match_cluster_for_text(index, whole_text, meanings)  # type: ignore[arg-type]

        assert cluster is not None
        assert cluster.cluster_id == "pc-reliable"
        assert index.match_many_calls != []  # 불신뢰라 짧은 경로로 끝나지 않고 후보까지 시도했다


class TestResultPersistence:
    """2026-09-22 UX 점검 회귀 — 조회 결과가 재실행 후에도 남아야 한다.

    예전 구조(`if not st.button("확인"): return` 아래에서 바로 렌더링)에서는 Streamlit
    버튼이 **클릭한 그 재실행에서만** True라서, 결과를 띄운 뒤 어떤 위젯이든 건드리면
    결과 전체가 사라졌다. 시연 중 화면이 비면 재조회에 또 몇 분이 걸리므로 실사용·발표
    양쪽에서 치명적이다.
    """

    def _card(self) -> card.ReportCard:
        return card.ReportCard(
            fact_check=FactCheckResult(
                claim="유상증자",
                items=[
                    FactCheckItem(
                        fact="정정공시로 발행가를 낮췄다",
                        supporting_section=None,
                        polarity="positive",
                    )
                ],
            ),
            phrase_stats=None,
        )

    def test_lookup_opens_dedicated_result_page(self, monkeypatch: pytest.MonkeyPatch) -> None:
        at = _run(monkeypatch, self._card(), text="조회할 공시 루머 문장입니다")
        assert not at.exception
        assert not at.selectbox
        assert not at.text_area
        assert any(title.value == "공시 대조 결과" for title in at.title)
        assert any(button.label == "다시 조회하기" for button in at.button)

    def test_round_trip_preserves_edited_draft_without_repeating_lookup(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        at = _run(monkeypatch, self._card(), text="처음 조회한 공시 문장입니다")

        def unexpected_lookup(*args, **kwargs):
            raise AssertionError("페이지 이동만으로 재조회하면 안 됩니다")

        monkeypatch.setattr(card, "build_report", unexpected_lookup)
        next(b for b in at.button if b.label == "다시 조회하기").click().run()
        assert at.selectbox[0].value == "삼성전자"
        assert at.text_area[0].value == "처음 조회한 공시 문장입니다"
        assert not any("정정공시로 발행가를 낮췄다" in m.value for m in at.markdown)
        at.text_area[0].input("돌아와서 수정한 공시 문장입니다").run()
        next(b for b in at.button if b.label == "직전 결과 보기").click().run()
        assert not at.exception
        assert any("이전 조회" in i.value for i in at.info)
        next(b for b in at.button if b.label == "다시 조회하기").click().run()
        assert at.text_area[0].value == "돌아와서 수정한 공시 문장입니다"

    def test_result_page_without_saved_result_has_recovery_action(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        at = _run(monkeypatch, self._card())
        del at.session_state[app_module._RESULT_KEY]
        at.run()
        assert not at.exception
        assert any("표시할 조회 결과가 없습니다" in i.value for i in at.info)
        next(b for b in at.button if b.label == "다시 조회하기").click().run()
        assert at.selectbox

    def test_results_survive_example_chip_click(self, monkeypatch: pytest.MonkeyPatch) -> None:
        at = _run(monkeypatch, self._card())
        assert any("정정공시로 발행가를 낮췄다" in m.value for m in at.markdown)

        next(b for b in at.button if b.label == "다시 조회하기").click().run()
        at.pills[0].set_value(_EXAMPLE_QUERIES[0]).run()
        next(b for b in at.button if b.label == "직전 결과 보기").click().run()

        assert not at.exception
        assert any("정정공시로 발행가를 낮췄다" in m.value for m in at.markdown)

    def test_results_survive_company_change(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # 기본 픽스처는 회사가 하나뿐이라 "다른 회사로 바꾸는" 조작 자체를 못 한다 —
        # 이 테스트에서만 두 개로 늘린다(autouse 픽스처 설정을 여기서 덮어쓴다).
        monkeypatch.setattr(
            candidates_module,
            "load_corpus_companies",
            lambda _raw_dir: {"00126380": "삼성전자", "00164779": "SK하이닉스"},
        )
        st.cache_resource.clear()

        at = _run(monkeypatch, self._card())
        assert any("정정공시로 발행가를 낮췄다" in m.value for m in at.markdown)

        next(b for b in at.button if b.label == "다시 조회하기").click().run()
        current = at.selectbox[0].value
        other = next(o for o in at.selectbox[0].options if o != current)
        at.selectbox[0].select(other).run()
        next(b for b in at.button if b.label == "직전 결과 보기").click().run()

        assert not at.exception
        assert any("정정공시로 발행가를 낮췄다" in m.value for m in at.markdown)

    # --- PR #113 리뷰(@9abin) 회귀: 보존된 결과가 "어떤 입력에 대한 것인지" 보여야 한다.
    # 결과를 남겨두기만 하면, A사 조회 후 B사로 바꿨을 때 A사 결과가 B사 결과처럼 읽힌다.

    def test_result_shows_company_and_text_it_was_queried_with(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        at = _run(monkeypatch, self._card(), text="조회 당시에 넣은 문장입니다")

        assert not at.exception
        context = " ".join(c.value for c in at.caption)
        assert "삼성전자" in context
        assert "조회 당시에 넣은 문장입니다" in " ".join(t.value for t in at.text)
        assert not any("이전 조회" in i.value for i in at.info)

    def test_company_change_marks_result_as_previous_lookup(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            candidates_module,
            "load_corpus_companies",
            lambda _raw_dir: {"00126380": "삼성전자", "00164779": "SK하이닉스"},
        )
        st.cache_resource.clear()

        at = _run(monkeypatch, self._card())
        next(b for b in at.button if b.label == "다시 조회하기").click().run()
        current = at.selectbox[0].value
        other = next(o for o in at.selectbox[0].options if o != current)
        at.selectbox[0].select(other).run()
        next(b for b in at.button if b.label == "직전 결과 보기").click().run()

        assert not at.exception
        assert any("이전 조회" in i.value for i in at.info)
        # 지금 고른 회사가 아니라 조회 당시 회사를 보여줘야 한다.
        assert current in " ".join(c.value for c in at.caption)

    def test_text_edit_marks_result_as_previous_lookup(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        at = _run(monkeypatch, self._card())
        next(b for b in at.button if b.label == "다시 조회하기").click().run()
        at.text_area[0].input("조회 후에 새로 고쳐 쓴 문장입니다").run()
        next(b for b in at.button if b.label == "직전 결과 보기").click().run()

        assert not at.exception
        assert any("이전 조회" in i.value for i in at.info)


class TestLookupFailureSurface:
    """2026-09-22 UX 점검 회귀 — LLM/네트워크 실패가 스택트레이스로 새어나가면 안 된다.

    `_build_card()`는 `NotImplementedError`만 잡는다. LLM 타임아웃(PR #109에서 90초
    상한)·레이트리밋·키 미설정은 그대로 올라와 화면에 파이썬 트레이스백이 그려졌다.
    """

    def test_llm_failure_shows_message_not_traceback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        at = _run(monkeypatch, RuntimeError("APITimeoutError: Request timed out"))

        assert not at.exception, "예외가 화면으로 새어나갔다"
        assert any("오류가 발생해" in e.value for e in at.error)

    def test_failure_detail_available_for_the_team(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """사용자용 문구로 갈음하되 원문을 버리진 않는다 — 조용히 삼키면 디버깅이 막힌다
        (`CLAUDE.md` 원칙: 예외를 잡되 반드시 로그·표시를 남긴다)."""
        at = _run(monkeypatch, RuntimeError("APITimeoutError: Request timed out"))

        assert any("기술적 상세" in e.label for e in at.expander)
        assert any("APITimeoutError" in c.value for c in at.code)

    def test_successful_retry_clears_previous_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """오류 뒤 재조회가 성공하면 오류 표시가 남아 있으면 안 된다."""
        at = _run(monkeypatch, RuntimeError("일시적 실패"))
        assert at.error

        monkeypatch.setattr(
            card,
            "build_report",
            lambda *a, **k: card.ReportCard(
                fact_check=FactCheckResult(
                    claim="루머",
                    items=[
                        FactCheckItem(
                            fact="복구된 사실", supporting_section=None, polarity="positive"
                        )
                    ],
                ),
                phrase_stats=None,
            ),
        )
        next(b for b in at.button if b.label == "다시 조회하기").click().run()
        _confirm_button(at).click().run()

        assert not at.exception
        assert at.error == []
        assert any("복구된 사실" in m.value for m in at.markdown)


class TestGlossarySearch:
    """2026-09-22 UX 점검 — 용어 20개를 평문 나열하면 찾는 용어까지 스크롤해야 한다."""

    def _glossary_query(self, at: AppTest):
        return next(t for t in at.text_input if t.label == "용어 검색")

    def test_filters_by_term_name(self) -> None:
        at = _app()
        at.run()
        term = next(iter(GLOSSARY.values())).term

        self._glossary_query(at).input(term).run()

        assert not at.exception
        assert any(term in m.value for m in at.markdown)

    def test_no_match_shows_guidance_not_empty_screen(self) -> None:
        at = _app()
        at.run()

        self._glossary_query(at).input("존재하지않는용어zzz").run()

        assert not at.exception
        assert any("일치하는 용어가 없습니다" in i.value for i in at.info)


class TestClusterMeaningsDiskCache:
    """서버를 다시 띄워도 클러스터 의미를 재계산하지 않는다 — 2026-09-25 응답시간 후속.

    실측(`docs/testing/response-time.md`)에서 서버 기동 후 첫 조회 5~9분 중 약 4분
    20초가 이 계산이었다. `@st.cache_resource`는 프로세스 안에서만 유효해 재시작하면
    사라지므로, 디스크 캐시가 없으면 매 기동마다 그 시간을 다시 쓴다.
    """

    def _prepare(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[int]:
        import numpy as np

        from disclosure_lit.dictionary.cluster_meaning import ClusterMeaning
        from disclosure_lit.retrieval.phrase_index import PhraseCluster

        section = _section("선제적 재무구조 개선을 위해 계약을 체결하였습니다.")
        cluster = PhraseCluster(
            cluster_id="pc-0001", representative_phrase="선제적 재무구조", sample_count=5
        )

        class _Index:
            def dump(self):
                return [cluster], np.zeros((1, 2), dtype="float32")

        monkeypatch.setattr(app_module, "_CACHE_DIR", tmp_path)
        monkeypatch.setattr(app_module, "_local_sections", lambda: [section])
        monkeypatch.setattr(app_module, "_phrase_bundle", lambda: (_Index(), []))
        monkeypatch.setattr(app_module, "select_clustering_pool", lambda _s: (["선제적"], {}))

        calls: list[int] = []

        def _counting_compute(index, phrases, sections):
            calls.append(1)
            return {
                "pc-0001": ClusterMeaning(
                    cluster_id="pc-0001",
                    representative_phrase="선제적 재무구조",
                    dominant_form_type="유상증자",
                    purity=0.8,
                    occurrence_count=5,
                    company_count=4,
                    is_reliable=True,
                )
            }

        monkeypatch.setattr(app_module, "compute_cluster_meanings", _counting_compute)
        return calls

    def test_second_process_reuses_disk_cache(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        calls = self._prepare(monkeypatch, tmp_path)

        first = app_module._cluster_meanings()
        assert len(calls) == 1, "첫 기동에서는 계산한다"

        st.cache_resource.clear()  # 프로세스 재시작과 같은 상태
        second = app_module._cluster_meanings()

        assert len(calls) == 1, "재기동에서 다시 계산했다 — 디스크 캐시가 안 쓰인 것"
        assert second == first

    def test_recomputes_when_corpus_gains_a_disclosure(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """코퍼스에 다른 공시가 들어오면 예전 결과를 쓰면 안 된다.

        지문이 보는 건 **어떤 공시가 들어 있는지**(rcept_no+corp_code)다 — 같은
        rcept_no의 본문만 바뀌는 경우(재청킹 등)는 `_sections_fingerprint` docstring이
        밝히듯 `_SCHEMA_VERSION`·파라미터 지문 쪽에서 잡는 몫이라, 이 캐시도 기존
        캐시들과 같은 규칙을 그대로 따른다.
        """
        calls = self._prepare(monkeypatch, tmp_path)
        app_module._cluster_meanings()

        other = DisclosureSection(
            rcept_no="R2",
            corp_code="00126380",
            form_type="유상증자",
            section_title="제목",
            text="다른 공시의 본문입니다.",
        )
        st.cache_resource.clear()
        sections = [_section("선제적 재무구조 개선을 위해 계약을 체결하였습니다."), other]
        monkeypatch.setattr(app_module, "_local_sections", lambda: sections)
        app_module._cluster_meanings()

        assert len(calls) == 2


class TestEvidenceRecency:
    """근거의 '시점'을 사실 그대로 보여준다 — 2026-09-30.

    최신성은 날짜라는 사실이라 규제 방어선과 충돌하지 않는다. 다만 **평가 표현은 쓰지
    않는다** — "오래된 루머입니다"는 무시해도 된다는 판단으로, "최신 정보입니다"는 신뢰도
    보증으로 읽힌다(CLAUDE.md 주의점 1). 날짜와 코퍼스 수집 범위까지만 보여주고 해석은
    사용자 몫으로 둔다.

    코퍼스 수집 종료일을 함께 적는 이유: 배치 수집이라 "가장 최근 근거가 8월"이라는 표시만
    보면 "그 뒤로 아무 일도 없었다"로 읽힌다. 실제로는 수집이 거기서 끝났을 뿐이다.
    """

    def _item(
        self, fact: str, rcept_dt: str, *, polarity: str = "positive", note: str | None = None
    ) -> FactCheckItem:
        section = DisclosureSection(
            rcept_no=f"R{rcept_dt}",
            corp_code="00126380",
            form_type="유상증자",
            section_title="제목",
            text="본문",
            rcept_dt=rcept_dt,
        )
        return FactCheckItem(
            fact=fact, supporting_section=section, polarity=polarity, next_update_note=note
        )

    def _card(self, items: list[FactCheckItem]) -> card.ReportCard:
        return card.ReportCard(
            fact_check=FactCheckResult(claim="유상증자", items=items), phrase_stats=None
        )

    def test_newest_evidence_is_shown_first(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """LLM이 돌려준 순서가 아니라 접수일 최신순으로 그린다(같은 날짜면 원래 순서 유지)."""
        items = [
            self._item("오래된 사실이다", "20240101"),
            self._item("가장 최근 사실이다", "20260505"),
            self._item("중간 사실이다", "20250303"),
        ]
        at = _run(monkeypatch, self._card(items))

        joined = "\n".join(m.value for m in at.markdown)
        positions = [
            joined.index(f) for f in ("가장 최근 사실이다", "중간 사실이다", "오래된 사실이다")
        ]
        assert positions == sorted(positions), "근거가 최신순으로 정렬되지 않았다"

    def test_shows_latest_evidence_date_with_corpus_cutoff(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """가장 최근 근거 날짜만 보여주면 '그 뒤로 아무 일 없었다'는 착시가 생긴다."""
        # AppTest는 스크립트를 새 네임스페이스로 실행하므로 `app_module` 속성을 패치해도
        # 닿지 않는다 — 이 파일의 다른 테스트와 같은 원칙으로 소스 모듈을 패치한다.
        corpus = [
            DisclosureSection("R1", "00126380", "유상증자", "제목", "본문", rcept_dt="20260916")
        ]
        monkeypatch.setattr(candidates_module, "load_all_local_sections", lambda _raw_dir: corpus)
        st.cache_resource.clear()

        at = _run(monkeypatch, self._card([self._item("사실이다", "20260814")]))

        captions = " ".join(c.value for c in at.caption)
        assert "2026-08-14" in captions, "가장 최근 근거 날짜가 없다"
        assert "2026-09-16" in captions, "코퍼스 수집 종료일이 함께 표시되지 않았다"

    def test_counts_facts_with_scheduled_update(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """재공시 예정은 '언제 다시 확인하면 되는지'를 알려주는 실용 정보라 눈에 띄어야 한다."""
        items = [
            self._item("사실 하나", "20260814", note="3개월 이내 재공시 예정"),
            self._item("사실 둘", "20260810", note="2026-12-08까지 재공시"),
            self._item("사실 셋", "20260801"),
        ]
        at = _run(monkeypatch, self._card(items))

        captions = " ".join(c.value for c in at.caption)
        assert "재공시 예정" in captions
        assert "2건" in captions, "재공시 예정이 명시된 사실 수가 표시되지 않았다"
