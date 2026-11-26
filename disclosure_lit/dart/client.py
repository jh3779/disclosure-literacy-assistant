"""DART OpenAPI(opendart.fss.or.kr) 클라이언트 — L0. 담당: A(이승노)."""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

BASE_URL = "https://opendart.fss.or.kr/api"
STATUS_OK = "000"
STATUS_NO_DATA = "013"  # 조회된 데이터 없음 — 오류가 아니라 빈 결과로 취급
MAX_PAGE_COUNT = 100  # DART list.json 한 페이지 최대 건수


class DartApiError(RuntimeError):
    """DART API 가 status != '000'(정상)/'013'(데이터 없음) 을 반환했을 때 던진다."""

    def __init__(self, status: str, message: str) -> None:
        super().__init__(f"DART API 오류(status={status}): {message}")
        self.status = status


class DartRateLimitError(DartApiError):
    """status='020'(요청 제한 초과, 분당 1,000건) — 재시도 대상."""


@dataclass
class DisclosureMeta:
    rcept_no: str
    corp_code: str
    corp_name: str
    report_nm: str
    rcept_dt: str


# company.json 의 corp_cls 값 — corpCode.xml 에는 이 구분이 아예 없다(필드 자체가 없음).
CORP_CLASS_KOSPI = "Y"
CORP_CLASS_KOSDAQ = "K"
CORP_CLASS_KONEX = "N"
CORP_CLASS_OTHER = "E"


@dataclass(frozen=True)
class CorpCodeEntry:
    """corpCode.xml 한 행 — DART 에 등록된 상장·비상장 전체 법인."""

    corp_code: str
    corp_name: str
    stock_code: str  # 빈 문자열이면 완전 비상장 — 그 외(코스피/코스닥/코넥스 등)는 전부 채워짐
    modify_date: str


# 실측(2026-09-18, 사용자 라이브 검증 — 삼성전자 공시 텍스트로 팩트체크 재현) —
# `extract_claim`(LLM)이 공시 본문의 "회사명" 필드를 그대로 읽어 "삼성전자주식회사"
# 처럼 법인격 표시가 붙은 이름을 뽑아낼 때가 있는데, DART corpCode.xml의 등록명은
# "삼성전자"처럼 붙지 않은 짧은 이름이다. exact-match만 쓰면 LLM 출력이 실행마다
# 미묘하게 달라질 때(법인격 표시 포함 여부) 같은 회사인데도 조회가 되다 안 되다
# 한다 — `run_factcheck`가 corp_code 해석 실패 시 조용히 빈 결과를 반환하므로
# (`pipeline.py::resolve_corp_code`), 사용자 입장에선 "이 회사는 공시가 없다"로
# 오인하기 쉽다. "주식회사"/"(주)"/"㈜" 표시를 지운 정규화 이름으로도 한 번 더
# 찾아보는 폴백을 추가한다 — 기존 exact-match 우선순위는 그대로 두고, 실패했을
# 때만 보조로 쓴다.
_CORP_SUFFIX = re.compile(r"주식회사|\(주\)|㈜")


def _normalize_corp_name(name: str) -> str:
    return _CORP_SUFFIX.sub("", name).strip()


class CorpCodeIndex:
    """종목명·종목코드 -> corp_code 조회.

    **주의**: `stock_code` 가 있는지(=상장 여부)만 거른다. `corpCode.xml` 응답에는
    시장구분 필드가 아예 없어서(공식 개발가이드 확인: corp_code/corp_name/
    corp_eng_name/stock_code/modify_date 5개뿐) 코스피·코스닥·코넥스를 여기서 구분하지
    못한다 — 코넥스 상장사도 `stock_code`가 있으므로 이 필터를 통과한다.

    docs/DESIGN.md "확정 파라미터"의 스코프(코스피·코스닥만, 코넥스 제외)를 실제로
    만족하려면, 여기서 찾은 `corp_code`로 `DartClient.fetch_corp_class()`를 호출해
    `company.json` 의 `corp_cls`(Y=코스피/K=코스닥/N=코넥스/E=기타)를 개별 확인해야
    한다 — corp_code 전체(수천 건)를 미리 다 조회하는 건 비용이 커서 이 인덱스
    생성 시점에는 하지 않는다.
    """

    def __init__(self, entries: list[CorpCodeEntry]) -> None:
        listed = [e for e in entries if e.stock_code]
        self._by_name: dict[str, CorpCodeEntry] = {}
        self._by_normalized_name: dict[str, CorpCodeEntry] = {}
        self._by_stock_code: dict[str, CorpCodeEntry] = {}
        for entry in listed:
            # 동명 회사 충돌 시 먼저 들어온 것을 유지 (극히 드문 케이스)
            self._by_name.setdefault(entry.corp_name, entry)
            self._by_normalized_name.setdefault(_normalize_corp_name(entry.corp_name), entry)
            self._by_stock_code[entry.stock_code] = entry

    def __len__(self) -> int:
        return len(self._by_stock_code)

    def find_by_name(self, corp_name: str) -> CorpCodeEntry | None:
        entry = self._by_name.get(corp_name)
        if entry is not None:
            return entry
        return self._by_normalized_name.get(_normalize_corp_name(corp_name))

    def find_by_stock_code(self, stock_code: str) -> CorpCodeEntry | None:
        return self._by_stock_code.get(stock_code)


@dataclass(frozen=True)
class CompanyInfo:
    """company.json 응답 중 A10 코퍼스 구축(시장구분·업종 확인)에 필요한 필드만 담는다."""

    corp_code: str
    corp_name: str
    stock_code: str
    corp_cls: str  # Y=코스피/K=코스닥/N=코넥스/E=기타
    induty_code: str  # 업종코드(한국표준산업분류)


def _parse_corp_code_xml(xml_bytes: bytes) -> list[CorpCodeEntry]:
    root = ElementTree.fromstring(xml_bytes)
    return [
        CorpCodeEntry(
            corp_code=(node.findtext("corp_code") or "").strip(),
            corp_name=(node.findtext("corp_name") or "").strip(),
            stock_code=(node.findtext("stock_code") or "").strip(),
            modify_date=(node.findtext("modify_date") or "").strip(),
        )
        for node in root.findall("list")
    ]


@retry(
    retry=retry_if_exception_type(DartRateLimitError),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    reraise=True,
)
def _get_json(url: str, params: dict[str, str], *, allow_no_data: bool = True) -> dict:
    """`status='013'`(조회된 데이터 없음)을 정상으로 볼지(`allow_no_data`) 오류로 볼지 고른다.

    list.json 에서 '013'은 정상적인 결과(그 기간에 공시가 없을 뿐)지만, company.json
    처럼 corp_code 하나를 특정해서 조회하는 API 에서 '013'은 사실상 "그 corp_code 는
    존재하지 않는다"에 가까운 오류다 — 조용히 빈 값으로 넘기면 안 된다. 호출부가
    `allow_no_data=False`로 이 구분을 명시한다.
    """
    resp = httpx.get(url, params=params, timeout=10.0)
    resp.raise_for_status()
    body = resp.json()
    status = body.get("status", "?")
    ok_statuses = (STATUS_OK, STATUS_NO_DATA) if allow_no_data else (STATUS_OK,)
    if status in ok_statuses:
        return body
    message = body.get("message", "") or ""
    if status == "020":
        raise DartRateLimitError(status, message)
    raise DartApiError(status, message)


@retry(
    retry=retry_if_exception_type(DartRateLimitError),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    reraise=True,
)
def _fetch_zip_bytes(url: str, params: dict[str, str]) -> bytes:
    resp = httpx.get(url, params=params, timeout=30.0)
    resp.raise_for_status()
    content = resp.content
    if content.startswith(b"PK"):
        return content
    root = ElementTree.fromstring(content)
    status = root.findtext("status", default="?") or "?"
    message = root.findtext("message", default="") or ""
    if status == "020":
        raise DartRateLimitError(status, message)
    raise DartApiError(status, message)


def _fetch_zip_or_raise(url: str, params: dict[str, str], *, cache_path: Path | None) -> bytes:
    """zip 응답을 그대로 돌려주고, XML 오류 응답이면 DartApiError 를 던진다.

    corpCode.xml / document.xml 둘 다 "정상이면 zip, 오류면 XML" 형태로 응답하는
    공통 패턴이라 여기 모아둔다. status='020'(요청 제한 초과)이면 `_get_json`(list.json
    경로)과 대칭으로 재시도한다 — 종목 하나의 공시를 수십~수백 건 연속으로
    fetch_document 하는 배치(scripts/collect_disclosures.py)가 분당 1,000건 제한에
    제일 먼저 걸리는 지점이었다(리뷰 지적, PR #31).
    """
    if cache_path is not None and cache_path.exists():
        return cache_path.read_bytes()

    content = _fetch_zip_bytes(url, params)
    if cache_path is not None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(content)
    return content


class DartClient:
    """공시 목록 조회(list.json) + 원문 다운로드(document.xml) 래퍼."""

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise RuntimeError("DART_API_KEY is missing. Check .env.")
        self.api_key = api_key

    def list_disclosures(self, corp_code: str, *, bgn_de: str, end_de: str) -> list[DisclosureMeta]:
        """기간 내 특정 회사의 공시 목록을 조회한다. `list.json` 을 필요한 만큼 페이지네이션."""
        results: list[DisclosureMeta] = []
        page_no = 1
        while True:
            body = _get_json(
                f"{BASE_URL}/list.json",
                {
                    "crtfc_key": self.api_key,
                    "corp_code": corp_code,
                    "bgn_de": bgn_de,
                    "end_de": end_de,
                    "page_no": str(page_no),
                    "page_count": str(MAX_PAGE_COUNT),
                },
            )
            if body.get("status") == STATUS_NO_DATA:
                break
            results.extend(
                DisclosureMeta(
                    rcept_no=row["rcept_no"],
                    corp_code=row["corp_code"],
                    corp_name=row["corp_name"],
                    report_nm=row["report_nm"],
                    rcept_dt=row["rcept_dt"],
                )
                for row in body.get("list", [])
            )
            total_page = int(body.get("total_page") or 1)
            if page_no >= total_page:
                break
            page_no += 1
        return results

    def fetch_document(self, rcept_no: str, *, cache_path: Path | None = None) -> bytes:
        """공시 원문을 다운로드한다 (`document.xml`, zip 응답).

        zip 안에는 보통 `{rcept_no}.xml` 하나가 들어있지만 첨부서류가 있으면 여러 개일 수
        있다 — 이름순으로 정렬해 첫 번째 XML 파일을 대표 원문으로 반환한다. `cache_path`
        를 주면 로컬 캐시를 우선 사용한다(대량 수집 시 재다운로드 방지).
        """
        zip_content = _fetch_zip_or_raise(
            f"{BASE_URL}/document.xml",
            {"crtfc_key": self.api_key, "rcept_no": rcept_no},
            cache_path=cache_path,
        )
        with zipfile.ZipFile(io.BytesIO(zip_content)) as zf:
            xml_names = sorted(name for name in zf.namelist() if name.lower().endswith(".xml"))
            if not xml_names:
                raise DartApiError("?", f"zip 안에 XML 파일이 없습니다: {zf.namelist()}")
            return zf.read(xml_names[0])

    def fetch_corp_codes(self, *, cache_path: Path | None = None) -> list[CorpCodeEntry]:
        """DART 등록 전체 법인의 corp_code 매핑을 내려받는다 (corpCode.xml, zip 응답).

        `cache_path` 를 주면 파일이 있을 때 그걸 읽고, 없으면 내려받은 뒤 저장한다 — 응답이
        수만 건짜리 zip 이라 매 실행마다 다시 받을 필요가 없다. 상장·비상장 필터링은
        `CorpCodeIndex` 생성 시점에 한다.

        비정상 응답(zip 이 아닌 XML 오류)이면 `DartApiError` 를 던진다.
        """
        content = _fetch_zip_or_raise(
            f"{BASE_URL}/corpCode.xml", {"crtfc_key": self.api_key}, cache_path=cache_path
        )
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            xml_bytes = zf.read("CORPCODE.xml")
        return _parse_corp_code_xml(xml_bytes)

    def fetch_corp_class(self, corp_code: str) -> str:
        """단일 회사의 시장구분(`corp_cls`)을 `company.json` 으로 조회한다.

        `CorpCodeIndex`(=`corpCode.xml`)는 상장 여부만 알 뿐 코스피/코스닥/코넥스를
        구분 못 하므로, 정확한 스코프 판정이 필요하면 이 메서드로 개별 확인한다.
        반환값은 "Y"(코스피)/"K"(코스닥)/"N"(코넥스)/"E"(기타) 중 하나
        (`CORP_CLASS_KOSPI` 등 상수 참고).
        """
        return self.fetch_company_info(corp_code).corp_cls

    def fetch_company_info(self, corp_code: str) -> CompanyInfo:
        """단일 회사의 기업개황(`company.json`)을 조회한다.

        A10(코퍼스 구축)이 업종별 층화 표본을 뽑을 때 `induty_code`(업종코드)가
        필요해서 추가했다 — `fetch_corp_class`는 그중 `corp_cls`만 쓰는 얇은 래퍼.
        `_get_json`을 `allow_no_data=False`로 재사용해 요청 제한(020) 재시도를 그대로
        받는다(원래 이 메서드는 재시도가 없었는데, A10에서 수백 건 연속 호출하게 되면서
        PR #31 리뷰 때 발견했던 것과 같은 문제가 생길 수 있어 같이 고쳤다). company.json
        은 corp_code 하나를 특정 조회하는 API라 '013'을 list.json 과 달리 오류로 취급한다.
        """
        body = _get_json(
            f"{BASE_URL}/company.json",
            {"crtfc_key": self.api_key, "corp_code": corp_code},
            allow_no_data=False,
        )
        return CompanyInfo(
            corp_code=corp_code,
            corp_name=body.get("corp_name", "") or "",
            stock_code=body.get("stock_code", "") or "",
            corp_cls=body.get("corp_cls", "") or "",
            induty_code=body.get("induty_code", "") or "",
        )
