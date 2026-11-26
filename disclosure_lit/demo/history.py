"""현재 앱 프로세스의 조회 기록. 최신 30건을 조회 당시 스냅샷으로 보관한다."""

from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import RLock

import streamlit as st

from disclosure_lit.report.card import ReportCard

HISTORY_LIMIT = 30


@dataclass(frozen=True)
class MeaningSnapshot:
    reason: str | None = None
    graph_dot: str | None = None
    scope: str = ""


@dataclass(frozen=True)
class LookupResult:
    text: str
    corp_code: str
    corp_name: str
    card: ReportCard
    is_sample: bool
    meaning: MeaningSnapshot | None = None


@dataclass(frozen=True)
class HistoryEntry:
    record_id: int
    created_at: datetime
    result: LookupResult


class ResultHistory:
    """세션 간 공유되므로 잠금과 복사로 보관된 카드의 변형을 막는다."""

    def __init__(self, limit: int = HISTORY_LIMIT) -> None:
        if limit < 1:
            raise ValueError("limit must be positive")
        self._limit = limit
        self._entries: list[HistoryEntry] = []
        self._next_id = 1
        self._lock = RLock()

    def add(self, result: LookupResult) -> HistoryEntry:
        with self._lock:
            entry = HistoryEntry(self._next_id, datetime.now(UTC), deepcopy(result))
            self._next_id += 1
            self._entries.insert(0, entry)
            del self._entries[self._limit :]
            return deepcopy(entry)

    def entries(self) -> tuple[HistoryEntry, ...]:
        with self._lock:
            return deepcopy(tuple(self._entries))


@st.cache_resource(show_spinner=False)
def get_history() -> ResultHistory:
    """같은 앱 서버의 모든 세션에서 사용하며 서버 재시작·캐시 삭제 시 비워진다."""
    return ResultHistory()
