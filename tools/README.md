# DeepSeek 注册器

`deepseek_register.py` 默认使用 EmailMux（也可通过 `--emailtick-url` 换成其他邮箱站点），打开邮箱和 DeepSeek 注册页面，
轮询邮箱页面中的验证码，成功后输出一行现有 `ds_core.accounts` 格式的 JSONL。

## 使用

需要 Python 3.11+、`uv` 和 Chromium：

```powershell
uv run --with "playwright>=1.45,<2" python tools/deepseek_register.py
uv run --with "playwright>=1.45,<2" playwright install chromium
```

默认会显示浏览器窗口。EmailMux 当前可直接访问；若邮箱站点出现 Cloudflare/CAPTCHA，完成后脚本会继续。
注册完成后，把终端输出的账号记录加入管理面板的账号池，或使用 `registered_accounts.jsonl`
作为导入源。不要把该文件提交到版本库。

如果邮箱页面结构变化，可以覆盖选择器：

```powershell
just register-account --email-selector 'input[name="email"]' --message-selector '.messages'
```

脚本没有调用 EmailTick 未公开的私有 API；站点没有稳定公开 API 时，使用网页流程能避免
把未经验证的接口字段写死。验证码提取仅接受 5 至 8 位数字，并过滤常见年份和占位码。
