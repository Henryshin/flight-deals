"""Threads 웹 로그인 (처음 한 번). 전용 크롬 프로필에 로그인 상태를 저장한다.

창이 뜨면 afterwork.makes 로 로그인하고, 홈 피드가 보이면 창을 닫으면 끝.
이후 threads_web_agent.py 가 같은 프로필로 글을 임시저장한다.
"""
import os
from pathlib import Path
from playwright.sync_api import sync_playwright

PROFILE = Path(os.environ.get("LOCALAPPDATA", ".")) / "afterwork-threads-profile"

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        str(PROFILE), channel="chrome", headless=False,
        viewport=None, args=["--start-maximized"], locale="ko-KR",
    )
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto("https://www.threads.com/login")
    print("로그인 후 홈 피드가 보이면 브라우저 창을 닫으세요.")
    try:
        page.wait_for_event("close", timeout=0)
    except Exception:
        pass
    ctx.close()
print("저장됨:", PROFILE)
