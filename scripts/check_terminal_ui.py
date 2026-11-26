"""외부 API 없이 실제 Streamlit UI를 Chromium으로 검증하고 캡처한다.

uv run --with playwright python scripts/check_terminal_ui.py --output /tmp/terminal-ui
최초 1회: uv run --with playwright python -m playwright install chromium
"""

from __future__ import annotations

import argparse
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parent.parent


def settle(page) -> None:
    page.wait_for_function("!document.querySelector('[data-testid=stStatusWidget]')", timeout=30000)
    page.wait_for_function(
        "document.getAnimations().every(a => a.animationName !== 'paperFeed' || "
        "a.playState === 'finished')",
        timeout=3000,
    )
    page.wait_for_timeout(250)


def animations(page) -> list[str]:
    return page.evaluate("document.getAnimations().map(a => a.animationName).filter(Boolean)")


def capture(page, path: Path) -> None:
    # Streamlit의 내부 스크롤 영역까지 담는다. full_page만으로는 첫 뷰포트에서 잘린다.
    viewport = page.viewport_size
    height = int(page.locator(".stMainBlockContainer").bounding_box()["height"]) + 100
    page.set_viewport_size({"width": viewport["width"], "height": max(viewport["height"], height)})
    page.screenshot(path=path, full_page=True)
    page.set_viewport_size(viewport)


def check(url: str, output: Path) -> dict:
    results = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(viewport={"width": 1280, "height": 1024})
        # 폰트까지 로컬에서 제공되는지 확인한다.
        external = []

        def local_only(route):
            if route.request.url.startswith(url):
                route.continue_()
            else:
                external.append(route.request.url)
                route.abort()

        context.route("**/*", local_only)
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.goto(url)
        power = page.get_by_role("button", name="전원 켜기", exact=True)
        expect(power).to_be_visible()
        expect(page.locator(".st-key-crt textarea")).to_have_count(0)
        page.evaluate("document.fonts.ready")
        page.screenshot(path=output / "01-off.png", full_page=True)
        power.focus()
        page.keyboard.press("Enter")
        expect(page.locator(".st-key-crt textarea")).to_be_visible()
        results["power_on_animations"] = page.locator(".st-key-crt").evaluate(
            "el => getComputedStyle(el).animationName"
        )
        assert results["power_on_animations"] == "crtOn"
        expect(page.locator(".terminal-led.on")).to_be_visible()
        page.wait_for_timeout(800)
        page.screenshot(path=output / "02-on.png", full_page=True)
        assert page.evaluate('document.fonts.check("16px Pretendard")')
        assert page.evaluate('document.fonts.check("16px Galmuri11")')
        assert "Pretendard" in page.locator("textarea").evaluate(
            "el => getComputedStyle(el).fontFamily"
        )

        page.get_by_role("combobox").click()
        page.get_by_role("option", name="삼성전자", exact=True).click()
        settle(page)
        assert "crtOn" not in animations(page)
        page.get_by_text("유상증자를 검토 중이라는 루머가 있습니다.", exact=True).click()
        settle(page)
        assert "crtOn" not in animations(page)
        page.get_by_role("button", name="확인", exact=True).click()
        expect(page).to_have_url(url + "/result")
        expect(page.locator(".st-key-printout")).to_be_visible()
        expect(page.get_by_role("heading", name="공시 대조 결과", exact=True)).to_be_visible()
        expect(page.get_by_role("button", name="다시 조회하기", exact=True)).to_be_enabled()
        expect(page.locator(".st-key-monitor")).to_have_count(0)
        settle(page)
        assert "crtOn" not in animations(page)
        results["rerun_animations"] = animations(page)
        assert page.locator(".st-key-crt .st-key-printout").count() == 0
        paper = page.locator(".st-key-printout")
        results["paper_background"] = paper.evaluate("e => getComputedStyle(e).backgroundColor")
        assert results["paper_background"] == "rgb(250, 248, 242)"
        assert "Pretendard" in paper.locator("p").first.evaluate(
            "el => getComputedStyle(el).fontFamily"
        )
        assert (
            paper.locator('[data-testid="stCaptionContainer"]').first.evaluate(
                "el => getComputedStyle(el).opacity"
            )
            == "1"
        )
        capture(page, output / "03-result.png")
        page.get_by_role("button", name="다시 조회하기", exact=True).click()
        expect(page.locator("textarea")).to_have_value("유상증자를 검토 중이라는 루머가 있습니다.")
        page.locator("textarea").fill("입력만 바꿔도 이전 결과가 유지되는지 확인합니다.")
        page.locator("textarea").press("Tab")
        page.get_by_role("button", name="직전 결과 보기", exact=True).click()
        expect(paper.get_by_text("이전 조회 결과", exact=False)).to_be_visible()

        page.get_by_role("button", name="다시 조회하기", exact=True).click()
        expect(page.locator("textarea")).to_have_value(
            "입력만 바꿔도 이전 결과가 유지되는지 확인합니다."
        )
        page.locator("textarea").fill("오류 안내를 확인하는 테스트 문장입니다.")
        page.locator("textarea").press("Tab")
        page.get_by_role("button", name="확인", exact=True).click()
        expect(paper.get_by_text("조회 중 오류가 발생해", exact=False)).to_be_visible()
        expect(page.locator('[data-testid="stException"]')).to_have_count(0)
        capture(page, output / "04-error.png")

        mobile = context.new_page()
        mobile.set_viewport_size({"width": 400, "height": 860})
        mobile.emulate_media(reduced_motion="reduce")
        mobile.goto(url)
        mobile.get_by_role("button", name="전원 켜기", exact=True).click()
        expect(mobile.locator(".st-key-crt textarea")).to_be_visible()
        results["reduced_motion_animations"] = animations(mobile)
        assert "crtOn" not in results["reduced_motion_animations"]
        mobile.get_by_role("combobox").click()
        mobile.get_by_role("option", name="삼성전자", exact=True).click()
        mobile.get_by_text("유상증자를 검토 중이라는 루머가 있습니다.", exact=True).click()
        mobile.get_by_role("button", name="확인", exact=True).click()
        expect(mobile.locator(".st-key-printout")).to_be_visible()
        expect(mobile.get_by_role("heading", name="공시 대조 결과", exact=True)).to_be_visible()
        expect(mobile.get_by_role("button", name="다시 조회하기", exact=True)).to_be_enabled()
        settle(mobile)
        results["mobile_overflow"] = mobile.evaluate(
            "document.documentElement.scrollWidth > innerWidth"
        )
        assert not results["mobile_overflow"]
        capture(mobile, output / "05-mobile-result.png")
        mobile.get_by_role("button", name="다시 조회하기", exact=True).click()
        mobile.get_by_role("tab", name="사전", exact=True).click()
        expect(mobile.get_by_label("용어 검색", exact=True)).to_be_visible()
        capture(mobile, output / "06-glossary.png")

        bypass = context.new_page()
        bypass.goto(url + "/?power=on")
        expect(bypass.locator(".st-key-crt textarea")).to_be_visible()
        assert "crtOn" not in animations(bypass)
        results["external_requests"] = external
        results["page_errors"] = errors
        assert not external and not errors
        browser.close()
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("/tmp/terminal-ui"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="terminal-preview-") as tmp:
        directory = Path(tmp)
        shutil.copy(ROOT / "tests/fixtures/terminal_preview.py", directory / "app.py")
        shutil.copytree(ROOT / "disclosure_lit/demo/static", directory / "static")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        url = f"http://localhost:{port}"
        with (args.output / "server.log").open("w") as log:
            server = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "streamlit",
                    "run",
                    str(directory / "app.py"),
                    "--server.port",
                    str(port),
                    "--server.headless",
                    "true",
                    "--server.fileWatcherType",
                    "none",
                    "--browser.gatherUsageStats",
                    "false",
                ],
                cwd=ROOT,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            try:
                for _ in range(100):
                    try:
                        with urllib.request.urlopen(url + "/_stcore/health", timeout=1):
                            break
                    except (urllib.error.URLError, TimeoutError):
                        if server.poll() is not None:
                            raise RuntimeError("검증 서버 시작 실패 — server.log 참고") from None
                        time.sleep(0.1)
                else:
                    raise TimeoutError("검증 서버 준비 시간 초과")
                results = check(url, args.output)
                (args.output / "results.json").write_text(
                    json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                print(json.dumps(results, ensure_ascii=False, indent=2))
            finally:
                server.terminate()
                server.wait(timeout=10)


if __name__ == "__main__":
    main()
