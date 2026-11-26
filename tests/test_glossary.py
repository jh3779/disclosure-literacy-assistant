"""dictionary/glossary.py 단위 테스트 — 정적 용어 사전.

이슈 #95(3안-A) — 평가성 표현 금지 원칙이 실제로 지켜지는지, 용어 수·출처 표기가
빠짐없는지 검증한다.
"""

from __future__ import annotations

from disclosure_lit.dictionary.glossary import _BANNED_WORDS, GLOSSARY, find_glossary_terms


class TestGlossaryContent:
    def test_has_twenty_entries(self) -> None:
        assert len(GLOSSARY) == 20

    def test_every_entry_has_nonempty_definition_and_source(self) -> None:
        for entry in GLOSSARY.values():
            assert entry.term.strip()
            assert entry.definition.strip()
            assert entry.source.strip()

    def test_no_evaluative_words_in_definitions(self) -> None:
        """규제 방어선 — "유리/불리/위험" 등 평가성 표현이 정의 문구에 섞이면 안 된다
        (이슈 #95 원칙)."""
        offenders = [
            (entry.term, word)
            for entry in GLOSSARY.values()
            for word in _BANNED_WORDS
            if word in entry.definition
        ]
        assert offenders == []

    def test_no_duplicate_terms(self) -> None:
        terms = [entry.term for entry in GLOSSARY.values()]
        assert len(terms) == len(set(terms))

    def test_dict_key_matches_entry_term(self) -> None:
        for key, entry in GLOSSARY.items():
            assert key == entry.term


class TestFindGlossaryTerms:
    def test_finds_term_present_in_text(self) -> None:
        matches = find_glossary_terms("당사는 금번 유상증자를 통해 자금을 조달할 예정입니다.")
        assert any(entry.term == "유상증자" for entry in matches)

    def test_finds_multiple_terms(self) -> None:
        matches = find_glossary_terms(
            "전환사채와 신주인수권부사채를 함께 발행하기로 결정하였습니다."
        )
        terms = {entry.term for entry in matches}
        assert {"전환사채", "신주인수권부사채"} <= terms

    def test_no_match_returns_empty_list(self) -> None:
        assert find_glossary_terms("오늘 날씨가 맑습니다.") == []

    def test_empty_text_returns_empty_list(self) -> None:
        assert find_glossary_terms("") == []
