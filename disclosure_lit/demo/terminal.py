"""입력 단말기와 출력 프린터의 표시. 조회·결과 상태는 app.py가 소유한다."""

from functools import cache
from pathlib import Path

import streamlit as st

_STATIC = Path(__file__).with_name("static")
_ON = "_monitor_on"
_JUST_POWERED = "_just_powered"


@cache
def _css(name: str) -> str:
    return f"<style>{(_STATIC / name).read_text(encoding='utf-8')}</style>"


def prepare_terminal() -> bool:
    st.html(_css("retro.css"))
    st.session_state.setdefault(_ON, st.query_params.get("power") == "on")
    if st.session_state.pop(_JUST_POWERED, False):
        st.html(_css("power-on.css"))
    return st.session_state[_ON]


def _power_on() -> None:
    st.session_state[_ON] = True
    st.session_state[_JUST_POWERED] = True


def render_power_button() -> None:
    st.html(
        '<div class="terminal-idle">'
        '<div class="terminal-idle-symbol" aria-hidden="true">⏻</div>'
        '<p class="terminal-idle-title">공시를 읽는 또 하나의 방법</p>'
        "<p>전원을 켜고, 궁금한 문장을 입력하세요.</p></div>"
    )
    with st.container(key="power"):
        st.button("전원 켜기", key="terminal_power", on_click=_power_on)


def render_bezel(powered: bool) -> None:
    # 빈 st.container는 DOM에서 생략될 수 있으므로 실제 표시 요소로 LED를 그린다.
    state = "on" if powered else "off"
    label = "켜짐" if powered else "꺼짐"
    st.html(
        '<div class="terminal-bezel">'
        '<span>DISCLOSURE TERMINAL <span class="terminal-model">DT–01</span></span>'
        f'<span class="terminal-status"><i class="terminal-led {state}" '
        f'aria-hidden="true"></i>전원 {label}</span></div>'
    )


def render_printer_slot(*, animate: bool) -> None:
    """새 결과의 첫 렌더에만 출력 CSS를 주입한다. 재열람은 정지된 출력물이다."""
    if animate:
        st.html(_css("print-feed.css"))
    st.html('<div class="printer-mouth" aria-hidden="true"></div>')
