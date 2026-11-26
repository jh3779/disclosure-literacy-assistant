"""A4 — DartClient.fetch_document zip 파싱·오류·캐시 단위 테스트. 네트워크 호출 없음."""

from __future__ import annotations

import io
import time
import zipfile

import httpx
import pytest

from disclosure_lit.dart.client import DartApiError, DartClient

SAMPLE_DOC_XML = "<DOCUMENT><TITLE>임시주주총회소집공고</TITLE></DOCUMENT>"


def _zip_bytes(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def _fake_response(content: bytes) -> httpx.Response:
    request = httpx.Request("GET", "https://fake.test")
    return httpx.Response(200, content=content, request=request)


class TestFetchDocument:
    def test_returns_xml_bytes_from_zip(self, monkeypatch: pytest.MonkeyPatch) -> None:
        zip_content = _zip_bytes({"20260909000516.xml": SAMPLE_DOC_XML})

        def fake_get(url: str, *, params: dict, timeout: float) -> httpx.Response:
            assert params == {"crtfc_key": "test-key", "rcept_no": "20260909000516"}
            return _fake_response(zip_content)

        monkeypatch.setattr(httpx, "get", fake_get)

        result = DartClient("test-key").fetch_document("20260909000516")

        assert result.decode("utf-8") == SAMPLE_DOC_XML

    def test_picks_first_xml_when_multiple_attachments(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        zip_content = _zip_bytes(
            {
                "b_attachment.xml": "<DOCUMENT>첨부</DOCUMENT>",
                "a_main.xml": "<DOCUMENT>본문</DOCUMENT>",
            }
        )
        monkeypatch.setattr(httpx, "get", lambda *a, **k: _fake_response(zip_content))

        result = DartClient("test-key").fetch_document("rcept-1")

        # 파일명 정렬 순서상 a_main.xml 이 먼저 온다
        assert "본문" in result.decode("utf-8")

    def test_raises_on_error_response(self, monkeypatch: pytest.MonkeyPatch) -> None:
        error_xml = (
            "<result><status>013</status><message>조회된 데이터가 없습니다.</message></result>"
        ).encode()
        monkeypatch.setattr(httpx, "get", lambda *a, **k: _fake_response(error_xml))

        with pytest.raises(DartApiError) as exc_info:
            DartClient("test-key").fetch_document("no-such-rcept")
        assert exc_info.value.status == "013"

    def test_raises_when_zip_has_no_xml(self, monkeypatch: pytest.MonkeyPatch) -> None:
        zip_content = _zip_bytes({"readme.txt": "no xml here"})
        monkeypatch.setattr(httpx, "get", lambda *a, **k: _fake_response(zip_content))

        with pytest.raises(DartApiError):
            DartClient("test-key").fetch_document("rcept-1")

    def test_uses_cache_when_present(self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
        cache_path = tmp_path / "doc.zip"
        cache_path.write_bytes(_zip_bytes({"doc.xml": SAMPLE_DOC_XML}))

        def fail_get(*args, **kwargs):  # pragma: no cover - 호출되면 안 됨
            raise AssertionError("cache 가 있으면 네트워크 호출을 하면 안 된다")

        monkeypatch.setattr(httpx, "get", fail_get)

        result = DartClient("unused-key").fetch_document("rcept-1", cache_path=cache_path)
        assert result.decode("utf-8") == SAMPLE_DOC_XML

    def test_writes_cache_after_fetch(self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
        cache_path = tmp_path / "nested" / "doc.zip"
        zip_content = _zip_bytes({"doc.xml": SAMPLE_DOC_XML})
        monkeypatch.setattr(httpx, "get", lambda *a, **k: _fake_response(zip_content))

        DartClient("test-key").fetch_document("rcept-1", cache_path=cache_path)

        assert cache_path.exists()
        assert cache_path.read_bytes() == zip_content

    def test_rate_limit_retries_then_succeeds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """status='020'(요청 제한 초과)이면 list_disclosures 와 대칭으로 재시도해야 한다 —
        종목 하나당 공시를 수십~수백 건 연속 호출하는 배치(collect_disclosures.py)가
        분당 1,000건 제한에 제일 먼저 걸리는 지점이다."""
        monkeypatch.setattr(time, "sleep", lambda seconds: None)  # 지수 백오프 실제 대기 제거
        zip_content = _zip_bytes({"doc.xml": SAMPLE_DOC_XML})
        attempts = {"n": 0}

        def fake_get(url: str, *, params: dict, timeout: float) -> httpx.Response:
            attempts["n"] += 1
            if attempts["n"] < 3:
                error_xml = (
                    "<result><status>020</status><message>요청 제한 초과</message></result>"
                ).encode()
                return _fake_response(error_xml)
            return _fake_response(zip_content)

        monkeypatch.setattr(httpx, "get", fake_get)

        result = DartClient("test-key").fetch_document("rcept-1")

        assert attempts["n"] == 3
        assert result.decode("utf-8") == SAMPLE_DOC_XML

    def test_rate_limit_exhausts_retries(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(time, "sleep", lambda seconds: None)
        error_xml = (
            "<result><status>020</status><message>요청 제한 초과</message></result>"
        ).encode()
        monkeypatch.setattr(httpx, "get", lambda *a, **k: _fake_response(error_xml))

        with pytest.raises(DartApiError) as exc_info:
            DartClient("test-key").fetch_document("rcept-1")
        assert exc_info.value.status == "020"
