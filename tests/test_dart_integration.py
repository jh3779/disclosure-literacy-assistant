"""A9 — 실제 DART OpenAPI를 때리는 종단 테스트. `dart_integration` 마커로 표시돼 있어

`DART_API_KEY`가 없으면(conftest.py) 자동 스킵된다. 나머지 테스트(단위 테스트)는
전부 httpx 를 모킹해서 네트워크 없이 돌아가는 것과 대비된다 — 여기는 A3(list_disclosures)
+ A4(fetch_document) + A6(chunk_disclosure) 파이프라인이 실제로 끝까지 이어지는지
확인하는 최소한의 안전망이다.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from disclosure_lit.config import settings
from disclosure_lit.dart.client import DartClient
from disclosure_lit.dart.ingest import chunk_disclosure

SAMSUNG_ELECTRONICS_CORP_CODE = "00126380"


@pytest.mark.dart_integration
class TestDartPipelineEndToEnd:
    def test_list_fetch_chunk_pipeline_produces_sections(self) -> None:
        client = DartClient(settings.dart_api_key)
        end = date.today()
        bgn = end - timedelta(days=30)

        metas = client.list_disclosures(
            SAMSUNG_ELECTRONICS_CORP_CODE,
            bgn_de=bgn.strftime("%Y%m%d"),
            end_de=end.strftime("%Y%m%d"),
        )
        assert len(metas) > 0, "최근 30일간 삼성전자 공시가 하나도 없다면 뭔가 이상하다"

        meta = metas[0]
        raw = client.fetch_document(meta.rcept_no)
        assert len(raw) > 0

        sections = chunk_disclosure(raw, rcept_no=meta.rcept_no, corp_code=meta.corp_code)
        assert len(sections) >= 1
        assert all(s.text for s in sections), "빈 텍스트 섹션이 나오면 안 된다"
        assert all(s.rcept_no == meta.rcept_no for s in sections)

    def test_unknown_corp_code_returns_empty_not_error(self) -> None:
        """존재하지 않는 corp_code 는 DART 가 status=013(데이터 없음)으로 응답한다 —
        예외가 아니라 빈 리스트로 처리돼야 한다(A3 설계)."""
        client = DartClient(settings.dart_api_key)
        end = date.today()
        bgn = end - timedelta(days=30)

        metas = client.list_disclosures(
            "99999999", bgn_de=bgn.strftime("%Y%m%d"), end_de=end.strftime("%Y%m%d")
        )
        assert metas == []
