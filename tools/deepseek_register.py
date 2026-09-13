#!/usr/bin/env python3
"""Register one DeepSeek account using an EmailTick inbox.

EmailTick has no stable, publicly documented API. This module deliberately
uses its web UI through Playwright instead of guessing private endpoints.
Selectors are configurable because both sites occasionally change markup.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import secrets
import string
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin

from playwright.sync_api import (
    BrowserContext,
    Error as PlaywrightError,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)


EMAIL_RE = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
CODE_RE = re.compile(r"(?<!\d)(\d{5,8})(?!\d)")


def generate_browser_fingerprint() -> dict[str, object]:
    """选择与真实 Chromium 浏览器一致的常见浏览器参数。"""
    return {
        "screen": random.choice([
            {"width": 1920, "height": 1080},
            {"width": 1366, "height": 768},
            {"width": 1536, "height": 864},
            {"width": 1440, "height": 900},
        ]),
        "timezone": random.choice(["Asia/Taipei", "Asia/Shanghai", "Asia/Tokyo"]),
        "locale": "zh-TW",
    }


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


def environment_error_visible(page: Page) -> bool:
    pattern = re.compile(
        r"(?:\u5f53\u524d\u8bbe\u5907\u8fd0\u884c\u73af\u5883\u5f02\u5e38|"
        r"\u73af\u5883\u5f02\u5e38|\u8acb\u9ede\u64ca\u6253\u7834\u5716\u6848|"
        r"environment\s+(?:verification|error)|captcha)",
        re.I,
    )
    pattern = re.compile(r"当前设备运行环境异常(?:，请尝试更换环境)?")
    try:
        if page.get_by_text(pattern).first.is_visible(timeout=500):
            return True
    except PlaywrightTimeoutError:
        pass
    return bool(
        re.search(
            r"(?:\u5f53\u524d\u8bbe\u5907\u8fd0\u884c\u73af\u5883\u5f02\u5e38|"
            r"\u73af\u5883\u5f02\u5e38|\u8acb\u9ede\u64ca\u6253\u7834\u5716\u6848|"
            r"environment\s+(?:verification|error)|captcha)",
            page_text(page),
            re.I,
        )
    )


def wait_for_environment_error(page: Page, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if environment_error_visible(page):
            return True
        page.wait_for_timeout(200)
    return environment_error_visible(page)


def wait_for_environment_clear(page: Page, timeout: int) -> bool:
    print(
        "DeepSeek requires a browser verification. Complete it in the open browser; "
        f"waiting up to {timeout} seconds.",
        flush=True,
    )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not environment_error_visible(page):
            return True
        page.wait_for_timeout(500)
    return not environment_error_visible(page)


@dataclass
class RegisterOptions:
    emailtick_url: str
    deepseek_url: str
    timeout: int
    poll_interval: int
    headless: bool
    email_selector: str | None
    message_selector: str
    use_forgot_password_fallback: bool
    profile_dir: Path
    browser_channel: str
    manual_challenge_timeout: int


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


def registration_form(page: Page, email: str, pwd: str) -> tuple[object, object]:
    email_input = first_visible(page, [
        'input[type="email"]', 'input[autocomplete="email"]', 'input[placeholder*="邮箱"]',
        'input[placeholder*="email" i]'
    ])
    password_inputs = page.locator(
        'input[type="password"], input[autocomplete="new-password"], input[placeholder*="密码"]'
    )
    visible_passwords = []
    for index in range(password_inputs.count()):
        candidate = password_inputs.nth(index)
        try:
            if candidate.is_visible(timeout=700):
                visible_passwords.append(candidate)
        except PlaywrightTimeoutError:
            pass
    if not email_input or not visible_passwords:
        raise RuntimeError("无法定位 DeepSeek 注册表单；页面结构可能已变化")
    email_input.fill(email)
    visible_passwords[0].fill(pwd)
    if len(visible_passwords) > 1:
        visible_passwords[1].fill(pwd)
    return email_input, visible_passwords[0]


def forgot_password_code(page: Page, inbox_page: Page, options: RegisterOptions, email: str) -> str:
    forgot_url = urljoin(options.deepseek_url, "/forgot_password")
    page.goto(forgot_url, wait_until="domcontentloaded", timeout=options.timeout * 1000)
    page.wait_for_timeout(800)
    email_input = first_visible(page, [
        'input[type="email"]', 'input[autocomplete="email"]', 'input[placeholder*="邮箱"]',
        'input[placeholder*="手机号"]', 'input[placeholder*="email" i]'
    ])
    if not email_input:
        raise RuntimeError("无法定位忘记密码邮箱输入框；页面结构可能已变化")
    email_input.fill(email)
    if not click_text(page, [r"发送验证码", r"send.*code", r"verification"]):
        raise RuntimeError("无法从忘记密码页面发送验证码")
    click_text(inbox_page, [r"啟用", r"activate", r"開始接收"])
    return wait_for_code(inbox_page, options, email)


def register_deepseek(page: Page, inbox_page: Page, options: RegisterOptions, email: str, pwd: str) -> None:
    page.goto(options.deepseek_url, wait_until="domcontentloaded", timeout=options.timeout * 1000)
    page.wait_for_timeout(1500)
    click_text(page, [r"立即注册", r"sign\s*up", r"register", r"创建账号"])
    registration_form(page, email, pwd)
    if not click_text(page, [r"发送验证码", r"send.*code", r"verification"]):
        raise RuntimeError("无法定位发送验证码按钮")
    if wait_for_environment_error(page):
        if not options.use_forgot_password_fallback:
            raise RuntimeError(
                "DeepSeek registration triggered an environment verification challenge; "
                "forgot_password fallback is disabled. Complete verification in the browser "
                "or rerun with --forgot-password-fallback."
            )
        print("注册页提示环境异常，改用忘记密码流程获取验证码。", flush=True)
        code = forgot_password_code(page, inbox_page, options, email)
        page.goto(options.deepseek_url, wait_until="domcontentloaded", timeout=options.timeout * 1000)
        page.wait_for_timeout(1000)
        click_text(page, [r"立即注册", r"sign\s*up", r"register", r"创建账号"])
        registration_form(page, email, pwd)
    else:
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


def register_deepseek_with_manual_challenge(
    page: Page, inbox_page: Page, options: RegisterOptions, email: str, pwd: str
) -> None:
    page.goto(options.deepseek_url, wait_until="domcontentloaded", timeout=options.timeout * 1000)
    page.wait_for_timeout(1500)
    click_text(page, [r"sign\s*up", r"register", r"\u8a3b\u518a", r"\u6ce8\u518a"])
    registration_form(page, email, pwd)
    if not click_text(page, [r"send.*code", r"verification", r"\u9a57\u8b49\u78bc", r"\u9a57\u8b49"]):
        raise RuntimeError("Unable to locate the registration verification button")
    if wait_for_environment_error(page) and not wait_for_environment_clear(
        page, options.manual_challenge_timeout
    ):
        raise RuntimeError("The browser verification was not completed before timeout")
    click_text(inbox_page, [r"activate", r"\u555f\u7528", r"\u958b\u59cb"])
    code = wait_for_code(inbox_page, options, email)
    code_input = first_visible(
        page, ['input[autocomplete="one-time-code"]', 'input[placeholder*="code" i]', 'input[placeholder*="\u9a57\u8b49"]']
    )
    if not code_input:
        raise RuntimeError("Unable to locate the verification code input")
    code_input.fill(code)
    if not click_text(page, [r"sign\s*up", r"create account", r"\u8a3b\u518a", r"\u6ce8\u518a"]):
        raise RuntimeError("Unable to locate the final registration button")
    page.wait_for_timeout(3000)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--emailtick-url", default="https://emailmux.com/tw/temporary-gmail")
    parser.add_argument("--deepseek-url", default="https://chat.deepseek.com/sign_in")
    parser.add_argument("--output", type=Path, default=Path("registered_accounts.jsonl"))
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--poll-interval", type=int, default=3)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument(
        "--forgot-password-fallback",
        action="store_true",
        help="Enable the legacy forgot_password fallback when registration is challenged",
    )
    parser.add_argument(
        "--profile-dir",
        type=Path,
        default=Path(".playwright/deepseek-profile"),
        help="Persistent browser profile for cookies and device state",
    )
    parser.add_argument(
        "--browser-channel",
        default="chrome",
        help="Browser channel to use (default: chrome; use chromium if unavailable)",
    )
    parser.add_argument(
        "--manual-challenge-timeout",
        type=int,
        default=180,
        help="Seconds to wait for manual environment verification",
    )
    parser.add_argument("--email-selector", default="#email", help="邮箱地址显示元素 CSS selector")
    parser.add_argument("--message-selector", default="body", help="EmailTick 邮件内容 CSS selector")
    args = parser.parse_args()
    options = RegisterOptions(args.emailtick_url, args.deepseek_url, args.timeout, args.poll_interval,
                              args.headless, args.email_selector, args.message_selector,
                              args.forgot_password_fallback, args.profile_dir,
                              args.browser_channel, args.manual_challenge_timeout)
    pwd = make_password()
    with sync_playwright() as playwright:
        options.profile_dir.mkdir(parents=True, exist_ok=True)
        fingerprint = generate_browser_fingerprint()
        launch_args = {
            "user_data_dir": str(options.profile_dir),
            "headless": options.headless,
            "slow_mo": 500,
            "viewport": fingerprint["screen"],
            "timezone_id": fingerprint["timezone"],
            "locale": fingerprint["locale"],
            "color_scheme": random.choice(["light", "dark", "no-preference"]),
            "extra_http_headers": {
                "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
            },
            "args": ["--disable-blink-features=AutomationControlled"],
        }
        if options.browser_channel:
            launch_args["channel"] = options.browser_channel
        try:
            context: BrowserContext = playwright.chromium.launch_persistent_context(**launch_args)
        except PlaywrightError:
            if options.browser_channel != "chrome":
                raise
            print("Chrome channel unavailable; falling back to Playwright Chromium.", file=sys.stderr, flush=True)
            launch_args.pop("channel", None)
            context = playwright.chromium.launch_persistent_context(**launch_args)
        try:
            context.add_init_script(
                """
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
                Object.defineProperty(navigator, 'languages', {
                    get: () => ['zh-TW', 'zh', 'en-US', 'en']
                });
                window.chrome = window.chrome || {runtime: {}, app: {}};
                const originalQuery = navigator.permissions.query.bind(navigator.permissions);
                navigator.permissions.query = (parameters) => (
                    parameters.name === 'notifications'
                        ? Promise.resolve({state: Notification.permission})
                        : originalQuery(parameters)
                );
                """
            )
            inbox_page = context.new_page()
            email = obtain_email(inbox_page, options)
            deepseek_page = context.new_page()
            if options.use_forgot_password_fallback:
                register_deepseek(deepseek_page, inbox_page, options, email, pwd)
            else:
                register_deepseek_with_manual_challenge(deepseek_page, inbox_page, options, email, pwd)
            record = {"email": email, "mobile": "", "area_code": "", "password": pwd, "device_id": ""}
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            print(json.dumps(record, ensure_ascii=False), flush=True)
            return 0
        finally:
            context.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("已取消", file=sys.stderr)
        raise SystemExit(130)
