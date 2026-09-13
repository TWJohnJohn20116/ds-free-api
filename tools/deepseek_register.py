#!/usr/bin/env python3
"""Register one DeepSeek account using an EmailTick inbox.

EmailTick has no stable, publicly documented API. This module deliberately
uses its web UI through Playwright instead of guessing private endpoints.
Selectors are configurable because both sites occasionally change markup.
"""

from __future__ import annotations

import argparse
import json
import re
import secrets
import string
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import BrowserContext, Page, TimeoutError as PlaywrightTimeoutError, sync_playwright


EMAIL_RE = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
CODE_RE = re.compile(r"(?<!\d)(\d{5,8})(?!\d)")


def make_password(length: int = 14) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    value = [secrets.choice(string.ascii_uppercase), secrets.choice(string.ascii_lowercase),
             secrets.choice(string.digits), secrets.choice("!@#$%^&*")]
    value.extend(secrets.choice(alphabet) for _ in range(max(0, length - len(value))))
    secrets.SystemRandom().shuffle(value)
    return "".join(value)


def first_visible(page: Page, selectors: list[str]):
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            if locator.is_visible(timeout=700):
                return locator
        except PlaywrightTimeoutError:
            pass
    return None


def click_text(page: Page, patterns: list[str]) -> bool:
    for pattern in patterns:
        locator = page.get_by_text(re.compile(pattern, re.I)).first
        try:
            if locator.is_visible(timeout=700):
                locator.click()
                return True
        except PlaywrightTimeoutError:
            pass
    return False


def page_text(page: Page) -> str:
    try:
        return page.locator("body").inner_text(timeout=3000)
    except PlaywrightTimeoutError:
        return ""


@dataclass
class RegisterOptions:
    emailtick_url: str
    deepseek_url: str
    timeout: int
    poll_interval: int
    headless: bool
    email_selector: str | None
    message_selector: str


def obtain_email(page: Page, options: RegisterOptions) -> str:
    page.goto(options.emailtick_url, wait_until="domcontentloaded", timeout=options.timeout * 1000)
    print("EmailTick 页面已打开；若出现 Cloudflare/CAPTCHA，请在浏览器中完成。", flush=True)
    page.wait_for_timeout(1500)
    click_text(page, [r"generate", r"create.*mail", r"new.*inbox", r"生成邮箱", r"创建邮箱", r"隨機"])
    if options.email_selector:
        candidate = page.locator(options.email_selector).first
        try:
            candidate.wait_for(state="visible", timeout=options.timeout * 1000)
            value = candidate.input_value() if candidate.evaluate("el => ['INPUT', 'TEXTAREA'].includes(el.tagName)") else (candidate.text_content() or "")
            if EMAIL_RE.fullmatch(value.strip()):
                return value.strip().lower()
        except PlaywrightTimeoutError:
            pass
    deadline = time.monotonic() + options.timeout
    while time.monotonic() < deadline:
        match = EMAIL_RE.search(page_text(page))
        if match:
            return match.group(0).lower()
        page.wait_for_timeout(1000)
    raise RuntimeError("无法从 EmailTick 页面识别邮箱地址；请设置 --email-selector")


def wait_for_code(page: Page, options: RegisterOptions, email: str) -> str:
    deadline = time.monotonic() + options.timeout
    while time.monotonic() < deadline:
        text = page_text(page)
        try:
            text += "\n" + "\n".join(page.locator(options.message_selector).all_inner_texts())
        except Exception:
            pass
        for match in CODE_RE.finditer(text):
            code = match.group(1)
            if code not in {"2024", "2025", "2026", "2027", "123456"}:
                return code
        page.reload(wait_until="domcontentloaded", timeout=options.timeout * 1000)
        page.wait_for_timeout(options.poll_interval * 1000)
    raise RuntimeError(f"等待 {email} 的 DeepSeek 验证码超时")


def register_deepseek(page: Page, inbox_page: Page, options: RegisterOptions, email: str, pwd: str) -> None:
    page.goto(options.deepseek_url, wait_until="domcontentloaded", timeout=options.timeout * 1000)
    page.wait_for_timeout(1500)
    click_text(page, [r"立即注册", r"sign\s*up", r"register", r"创建账号"])
    email_input = first_visible(page, [
        'input[type="email"]', 'input[autocomplete="email"]', 'input[placeholder*="邮箱"]',
        'input[placeholder*="email" i]'
    ])
    password_input = first_visible(page, [
        'input[type="password"]', 'input[autocomplete="new-password"]', 'input[placeholder*="密码"]'
    ])
    if not email_input or not password_input:
        raise RuntimeError("无法定位 DeepSeek 注册表单；页面结构可能已变化")
    email_input.fill(email)
    password_input.fill(pwd)
    confirm = first_visible(page, ['input[autocomplete="new-password"]:nth-of-type(2)', 'input[placeholder*="确认"]'])
    if confirm:
        confirm.fill(pwd)
    if not click_text(page, [r"发送验证码", r"send.*code", r"verification"]):
        raise RuntimeError("无法定位发送验证码按钮")
    # EmailMux starts polling only after the mailbox is activated.
    click_text(inbox_page, [r"啟用", r"activate", r"開始接收"])
    code = wait_for_code(inbox_page, options, email)
    code_input = first_visible(page, ['input[autocomplete="one-time-code"]', 'input[placeholder*="验证码"]', 'input[placeholder*="code" i]'])
    if not code_input:
        raise RuntimeError("无法定位验证码输入框")
    code_input.fill(code)
    if not click_text(page, [r"注册", r"sign\s*up", r"create account"]):
        raise RuntimeError("无法定位最终注册按钮")
    page.wait_for_timeout(3000)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--emailtick-url", default="https://emailmux.com/tw/temporary-gmail")
    parser.add_argument("--deepseek-url", default="https://chat.deepseek.com/sign_in")
    parser.add_argument("--output", type=Path, default=Path("registered_accounts.jsonl"))
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--poll-interval", type=int, default=3)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--email-selector", default="#email", help="邮箱地址显示元素 CSS selector")
    parser.add_argument("--message-selector", default="body", help="EmailTick 邮件内容 CSS selector")
    args = parser.parse_args()
    options = RegisterOptions(args.emailtick_url, args.deepseek_url, args.timeout, args.poll_interval,
                              args.headless, args.email_selector, args.message_selector)
    pwd = make_password()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=options.headless)
        context: BrowserContext = browser.new_context()
        try:
            inbox_page = context.new_page()
            email = obtain_email(inbox_page, options)
            deepseek_page = context.new_page()
            register_deepseek(deepseek_page, inbox_page, options, email, pwd)
            record = {"email": email, "mobile": "", "area_code": "", "password": pwd, "device_id": ""}
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            print(json.dumps(record, ensure_ascii=False), flush=True)
            return 0
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("已取消", file=sys.stderr)
        raise SystemExit(130)
