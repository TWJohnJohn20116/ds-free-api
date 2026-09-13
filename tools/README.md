# DeepSeek 注册器

`deepseek_register.py` 默认使用 EmailMux（也可通过 `--emailtick-url` 换成其他邮箱站点），打开邮箱和 DeepSeek 注册页面，
轮询邮箱页面中的验证码，成功后输出一行现有 `ds_core.accounts` 格式的 JSONL。

## 使用

后台面板使用的注册脚本已嵌入程序；启动时写入配置文件所在目录的 `.ds-free-api` 子目录，
因此不依赖从哪个工作目录启动，也不要求发布包额外携带 `tools/` 文件夹。
Windows Release 提供两个版本：普通 ZIP 不包含 Chromium，适合已经安装 Playwright Chromium
或希望首次运行时自动下载的环境；`*-with-chromium.zip` 已包含 `playwright-browsers/`，
请保持该目录与 `ds-free-api.exe` 同级，适合离线或不想在首次运行时下载的环境。

需要 Python 3.11+ 和 Chromium。后台会优先使用 `uv`，没有 `uv` 时自动回退到 `python` / `py`；也可以用 `DS_REGISTER_RUNNER` 指定运行程序。

```powershell
uv run --with "playwright>=1.45,<2" python tools/deepseek_register.py
uv run --with "playwright>=1.45,<2" playwright install chromium
```

没有 `uv` 时先安装依赖：

```powershell
python -m pip install -r tools/requirements.txt
python -m playwright install chromium
```

默认会显示浏览器窗口。EmailMux 当前可直接访问；若邮箱站点出现 Cloudflare/CAPTCHA，完成后脚本会继续。
注册完成后，把终端输出的账号记录加入管理面板的账号池，或使用 `registered_accounts.jsonl`
作为导入源。不要把该文件提交到版本库。

如果邮箱页面结构变化，可以覆盖选择器：

```powershell
just register-account --email-selector 'input[name="email"]' --message-selector '.messages'
```

注册页面触发环境风控时，脚本默认不会跳转到 `forgot_password` 验证码页面，而是直接在当前浏览器等待人工完成验证。
如需启用旧的兜底流程，显式添加 `--forgot-password-fallback`。

脚本没有调用 EmailTick 未公开的私有 API；站点没有稳定公开 API 时，使用网页流程能避免
把未经验证的接口字段写死。验证码提取仅接受 5 至 8 位数字，并过滤常见年份和占位码。
