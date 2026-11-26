"""A3 — DartClient.list_disclosures 페이지네이션·오류·재시도 단위 테스트. 네트워크 호출 없음."""

from __future__ import annotations

import time

import httpx
import pytest

from disclosure_lit.dart.client import DartApiError, DartClient


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    # 재시도 테스트가 tenacity 의 지수 백오프(최대 30초)를 실제로 기다리지 않게 한다.
    # tenacity.nap.sleep() 내부가 결국 time.sleep() 을 호출하므로 여기를 막는다 —
    # `@retry` 데코레이터의 기본 sleep 인자는 임포트 시점에 함수 객체로 바인딩돼 있어
    # tenacity.nap.sleep 자체를 패치해도 이미 캡처된 참조에는 반영되지 않는다.
    monkeypatch.setattr(time, "sleep", lambda seconds: None)


def _fake_response(json_body: dict) -> httpx.Response:
    request = httpx.Request("GET", "https://fake.test")
    return httpx.Response(200, json=json_body, request=request)


def _row(rcept_no: str) -> dict:
    return {
        "rcept_no": rcept_no,
        "corp_code": "00126380",
        "corp_name": "삼성전자",
        "report_nm": "분기보고서",
        "rcept_dt": "20260101",
    }


class TestListDisclosures:
    def test_single_page(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = []

        def fake_get(url: str, *, params: dict, timeout: float) -> httpx.Response:
            calls.append(params)
            return _fake_response(
                {
                    "status": "000",
                    "message": "정상",
                    "page_no": 1,
                    "total_page": 1,
                    "total_count": 1,
                    "list": [_row("20260101000001")],
                }
            )

        monkeypatch.setattr(httpx, "get", fake_get)

        result = DartClient("test-key").list_disclosures(
            "00126380", bgn_de="20260101", end_de="20260111"
        )

        assert len(result) == 1
        assert result[0].rcept_no == "20260101000001"
        assert len(calls) == 1
        assert calls[0]["page_no"] == "1"

    def test_paginates_until_last_page(self, monkeypatch: pytest.MonkeyPatch) -> None:
        pages = {
            1: {"status": "000", "total_page": 2, "list": [_row("A"), _row("B")]},
            2: {"status": "000", "total_page": 2, "list": [_row("C")]},
        }

        def fake_get(url: str, *, params: dict, timeout: float) -> httpx.Response:
            return _fake_response(pages[int(params["page_no"])])

        monkeypatch.setattr(httpx, "get", fake_get)

        result = DartClient("test-key").list_disclosures(
            "00126380", bgn_de="20260101", end_de="20260201"
        )

        assert [r.rcept_no for r in result] == ["A", "B", "C"]

    def test_no_data_returns_empty_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        body = {"status": "013", "message": "조회된 데이터가 없습니다."}
        monkeypatch.setattr(httpx, "get", lambda *a, **k: _fake_response(body))

        result = DartClient("test-key").list_disclosures(
            "00126380", bgn_de="20260101", end_de="20260102"
        )
        assert result == []

    def test_non_recoverable_error_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            httpx,
            "get",
            lambda *a, **k: _fake_response({"status": "010", "message": "등록되지 않은 키"}),
        )

        with pytest.raises(DartApiError) as exc_info:
            DartClient("bad-key").list_disclosures("00126380", bgn_de="20260101", end_de="20260102")
        assert exc_info.value.status == "010"

    def test_rate_limit_retries_then_succeeds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        attempts = {"n": 0}

        def fake_get(url: str, *, params: dict, timeout: float) -> httpx.Response:
            attempts["n"] += 1
            if attempts["n"] < 3:
                return _fake_response({"status": "020", "message": "요청 제한 초과"})
            return _fake_response({"status": "000", "total_page": 1, "list": [_row("X")]})

        monkeypatch.setattr(httpx, "get", fake_get)

        result = DartClient("test-key").list_disclosures(
            "00126380", bgn_de="20260101", end_de="20260102"
        )

        assert attempts["n"] == 3
        assert [r.rcept_no for r in result] == ["X"]

    def test_rate_limit_exhausts_retries(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            httpx,
            "get",
            lambda *a, **k: _fake_response({"status": "020", "message": "요청 제한 초과"}),
        )

        with pytest.raises(DartApiError) as exc_info:
            DartClient("test-key").list_disclosures(
                "00126380", bgn_de="20260101", end_de="20260102"
            )
        assert exc_info.value.status == "020"
