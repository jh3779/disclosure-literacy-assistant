"""브라우저 UI 검사 전용. 외부 데이터·LLM만 고정하고 실제 app.py를 실행한다."""

import runpy
from pathlib import Path

from disclosure_lit.dart.client import DartClient
from disclosure_lit.dart.ingest import DisclosureSection
from disclosure_lit.dictionary import candidates
from disclosure_lit.factcheck.verify import FactCheckItem, FactCheckResult
from disclosure_lit.report import card

candidates.load_all_local_sections = lambda _: []
candidates.load_sections_jsonl = lambda _: []
candidates.load_corpus_companies = lambda _: {"00126380": "삼성전자", "00164779": "SK하이닉스"}
DartClient.fetch_corp_codes = lambda *args, **kwargs: []


def preview_report(text, **kwargs):
    if "오류" in text:
        raise TimeoutError("UI 검증용 오류")
    section = DisclosureSection(
        rcept_no="20260922000001",
        corp_code=kwargs["corp_code_override"],
        form_type="주요사항보고서",
        section_title="유상증자 결정 (UI 검증용 고정 데이터)",
        text="제3자배정 방식의 신주 발행을 결정했습니다. 발행 조건은 확정되지 않았습니다.",
        rcept_dt="20260922",
    )
    return card.ReportCard(
        fact_check=FactCheckResult(
            claim=text,
            items=[
                FactCheckItem(
                    fact="신주 발행을 결정했다는 내용이 공시에 기재돼 있습니다.",
                    supporting_section=section,
                    polarity="positive",
                ),
                FactCheckItem(
                    fact="발행 조건은 확정되지 않았다고 명시돼 있습니다.",
                    supporting_section=section,
                    polarity="negative",
                ),
            ],
        ),
        phrase_stats=None,
    )


card.build_report = preview_report
runpy.run_path(str(Path.cwd() / "disclosure_lit/demo/app.py"), run_name="__main__")
