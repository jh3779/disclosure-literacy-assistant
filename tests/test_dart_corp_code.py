"""A2 — corpCode.xml 다운로드·파싱, CorpCodeIndex 필터링 단위 테스트. 네트워크 호출 없음."""

from __future__ import annotations

import io
import time
import zipfile

import httpx
import pytest

from disclosure_lit.dart.client import (
    CORP_CLASS_KONEX,
    CORP_CLASS_KOSDAQ,
    CORP_CLASS_KOSPI,
    CorpCodeEntry,
    CorpCodeIndex,
    DartApiError,
    DartClient,
)

SAMPLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<result>
  <list>
    <corp_code>00126380</corp_code>
    <corp_name>삼성전자</corp_name>
    <corp_eng_name>SAMSUNG ELECTRONICS CO,.LTD</corp_eng_name>
    <stock_code>005930</stock_code>
    <modify_date>20240101</modify_date>
  </list>
  <list>
    <corp_code>00164779</corp_code>
    <corp_name>비상장회사</corp_name>
    <corp_eng_name></corp_eng_name>
    <stock_code></stock_code>
    <modify_date>20240101</modify_date>
  </list>
</result>
"""


def _zip_bytes(inner_xml: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("CORPCODE.xml", inner_xml)
    return buf.getvalue()


def _fake_response(content: bytes) -> httpx.Response:
    # raise_for_status() 는 request 가 붙어 있어야 (성공 응답이어도) RuntimeError 없이 동작한다.
    request = httpx.Request("GET", "https://fake.test")
    return httpx.Response(200, content=content, request=request)


class TestCorpCodeIndex:
    def test_filters_out_fully_unlisted_companies(self) -> None:
        """stock_code 가 아예 없는(완전 비상장) 회사만 걸러진다 — 코스피/코스닥/코넥스는
        구분하지 못한다(corpCode.xml 에 그 필드 자체가 없음)."""
        entries = [
            CorpCodeEntry("00126380", "삼성전자", "005930", "20240101"),
            CorpCodeEntry("00164779", "비상장회사", "", "20240101"),
        ]
        index = CorpCodeIndex(entries)

        assert len(index) == 1
        assert index.find_by_name("삼성전자") is not None
        assert index.find_by_name("비상장회사") is None
        assert index.find_by_stock_code("005930") is not None

    def test_konex_company_passes_the_stock_code_filter_too(self) -> None:
        """코넥스 상장사도 stock_code 를 갖고 있어서 이 필터만으로는 코스피/코스닥과
        구분되지 않는다 — 정확한 시장구분은 DartClient.fetch_corp_class() 가 담당한다
        (PR #22 리뷰 지적사항)."""
        konex_entry = CorpCodeEntry("00999999", "코넥스회사", "900000", "20240101")
        index = CorpCodeIndex([konex_entry])

        assert len(index) == 1
        assert index.find_by_stock_code("900000") == konex_entry

    def test_unknown_lookup_returns_none(self) -> None:
        index = CorpCodeIndex([])
        assert index.find_by_name("없는회사") is None
        assert index.find_by_stock_code("000000") is None

    def test_find_by_name_falls_back_to_suffix_normalized_match(self) -> None:
        """실측(2026-09-18) — extract_claim(LLM)이 공시 본문을 그대로 읽어
        "삼성전자주식회사"처럼 법인격 표시가 붙은 이름을 뽑을 때가 있는데,
        DART 등록명("삼성전자")엔 안 붙어 있어 exact-match만으론 조회가 실패했다."""
        entry = CorpCodeEntry("00126380", "삼성전자", "005930", "20240101")
        index = CorpCodeIndex([entry])

        assert index.find_by_name("삼성전자주식회사") == entry
        assert index.find_by_name("주식회사삼성전자") == entry
        assert index.find_by_name("삼성전자(주)") == entry
        assert index.find_by_name("㈜삼성전자") == entry

    def test_find_by_name_exact_match_still_takes_priority(self) -> None:
        """등록명 자체가 법인격 표시를 포함하는 회사가 실제로 있으면(드묾),
        exact-match를 정규화보다 먼저 시도해 그 회사를 정확히 찾는다."""
        suffixed = CorpCodeEntry("00000001", "가나다주식회사", "100000", "20240101")
        plain = CorpCodeEntry("00000002", "가나다", "100001", "20240101")
        index = CorpCodeIndex([suffixed, plain])

        assert index.find_by_name("가나다주식회사") == suffixed
        assert index.find_by_name("가나다") == plain


class TestFetchCorpCodes:
    def test_parses_zip_response(self, monkeypatch: pytest.MonkeyPatch) -> None:
        zip_content = _zip_bytes(SAMPLE_XML)

        def fake_get(url: str, *, params: dict, timeout: float) -> httpx.Response:
            assert params["crtfc_key"] == "test-key"
            return _fake_response(zip_content)

        monkeypatch.setattr(httpx, "get", fake_get)

        entries = DartClient("test-key").fetch_corp_codes()

        assert len(entries) == 2
        assert entries[0] == CorpCodeEntry("00126380", "삼성전자", "005930", "20240101")

    def test_raises_on_error_response(self, monkeypatch: pytest.MonkeyPatch) -> None:
        error_xml = (
            "<result><status>010</status><message>등록되지 않은 키입니다.</message></result>"
        ).encode()

        def fake_get(url: str, *, params: dict, timeout: float) -> httpx.Response:
            return _fake_response(error_xml)

        monkeypatch.setattr(httpx, "get", fake_get)

        with pytest.raises(DartApiError) as exc_info:
            DartClient("bad-key").fetch_corp_codes()
        assert exc_info.value.status == "010"

    def test_uses_cache_when_present(self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
        cache_path = tmp_path / "corp_code.zip"
        cache_path.write_bytes(_zip_bytes(SAMPLE_XML))

        def fail_get(*args, **kwargs):  # pragma: no cover - 호출되면 안 됨
            raise AssertionError("cache 가 있으면 네트워크 호출을 하면 안 된다")

        monkeypatch.setattr(httpx, "get", fail_get)

        entries = DartClient("unused-key").fetch_corp_codes(cache_path=cache_path)
        assert len(entries) == 2

    def test_writes_cache_after_fetch(self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
        cache_path = tmp_path / "nested" / "corp_code.zip"
        zip_content = _zip_bytes(SAMPLE_XML)

        monkeypatch.setattr(httpx, "get", lambda *a, **k: _fake_response(zip_content))

        DartClient("test-key").fetch_corp_codes(cache_path=cache_path)

        assert cache_path.exists()
        assert cache_path.read_bytes() == zip_content


class TestFetchCorpClass:
    """company.json 으로 시장구분(corp_cls) 을 조회 — CorpCodeIndex 가 못 하는
    코스피/코스닥/코넥스 구분을 여기서 개별 확인한다(PR #22 리뷰 지적사항 대응)."""

    def test_returns_corp_cls(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_get(url: str, *, params: dict, timeout: float) -> httpx.Response:
            assert params == {"crtfc_key": "test-key", "corp_code": "00126380"}
            body = '{"status": "000", "message": "정상", "corp_cls": "Y"}'.encode()
            return _fake_response(body)

        monkeypatch.setattr(httpx, "get", fake_get)

        assert DartClient("test-key").fetch_corp_class("00126380") == CORP_CLASS_KOSPI

    def test_distinguishes_konex_from_kospi(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """CorpCodeIndex 필터는 코넥스도 통과시키지만, fetch_corp_class 는 구분해낸다."""
        body = b'{"status": "000", "corp_cls": "N"}'
        monkeypatch.setattr(httpx, "get", lambda *a, **k: _fake_response(body))

        assert DartClient("test-key").fetch_corp_class("00999999") == CORP_CLASS_KONEX

    def test_raises_on_error_response(self, monkeypatch: pytest.MonkeyPatch) -> None:
        body = '{"status": "013", "message": "조회된 데이터가 없습니다."}'.encode()
        monkeypatch.setattr(httpx, "get", lambda *a, **k: _fake_response(body))

        with pytest.raises(DartApiError) as exc_info:
            DartClient("test-key").fetch_corp_class("00000000")
        assert exc_info.value.status == "013"


class TestFetchCompanyInfo:
    """A10(코퍼스 구축)이 업종별 층화 표본을 뽑을 때 쓰는 induty_code 를 담아 반환한다."""

    def test_returns_corp_cls_and_induty_code(self, monkeypatch: pytest.MonkeyPatch) -> None:
        body = (
            '{"status": "000", "corp_name": "삼성전자", "stock_code": "005930", '
            '"corp_cls": "Y", "induty_code": "264"}'
        ).encode()
        monkeypatch.setattr(httpx, "get", lambda *a, **k: _fake_response(body))

        info = DartClient("test-key").fetch_company_info("00126380")

        assert info.corp_cls == CORP_CLASS_KOSPI
        assert info.induty_code == "264"
        assert info.corp_name == "삼성전자"

    def test_rate_limit_retries_then_succeeds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(time, "sleep", lambda seconds: None)
        attempts = {"n": 0}

        def fake_get(url: str, *, params: dict, timeout: float) -> httpx.Response:
            attempts["n"] += 1
            if attempts["n"] < 3:
                body = '{"status": "020", "message": "요청 제한 초과"}'.encode()
                return _fake_response(body)
            body = b'{"status": "000", "corp_cls": "K", "induty_code": "620"}'
            return _fake_response(body)

        monkeypatch.setattr(httpx, "get", fake_get)

        info = DartClient("test-key").fetch_company_info("00000001")

        assert attempts["n"] == 3
        assert info.corp_cls == CORP_CLASS_KOSDAQ

    def test_no_data_status_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """company.json 에서 '013'은 list.json 과 달리 정상적인 빈 결과가 아니라
        "그 corp_code 는 존재하지 않는다"에 가까운 오류로 취급해야 한다."""
        body = '{"status": "013", "message": "조회된 데이터가 없습니다."}'.encode()
        monkeypatch.setattr(httpx, "get", lambda *a, **k: _fake_response(body))

        with pytest.raises(DartApiError) as exc_info:
            DartClient("test-key").fetch_company_info("00000000")
        assert exc_info.value.status == "013"
