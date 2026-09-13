//! 后台自动注册任务：执行 Playwright 注册器并将成功账号热加入账号池。

use std::path::Path;
use std::process::Stdio;
use std::sync::Arc;

use serde::{Deserialize, Serialize};
use tokio::process::Command;
use tokio::sync::RwLock;

use crate::config::Account;

use super::handlers::AppState;

const REGISTRATION_SCRIPT: &str = include_str!("../../tools/deepseek_register.py");

#[derive(Debug, Clone, Serialize)]
pub(crate) struct RegistrationStatus {
    pub state: String,
    pub progress: u8,
    pub message: String,
    pub email: String,
}

#[derive(Debug, Deserialize, Default)]
pub(crate) struct RegistrationOptions {
    #[serde(default)]
    pub emailtick_url: Option<String>,
    #[serde(default)]
    pub headless: bool,
}

#[derive(Clone)]
pub(crate) struct RegistrationState {
    status: Arc<RwLock<RegistrationStatus>>,
}

impl RegistrationState {
    pub(crate) fn new() -> Self {
        Self {
            status: Arc::new(RwLock::new(RegistrationStatus {
                state: "idle".into(),
                progress: 0,
                message: "尚未开始注册".into(),
                email: String::new(),
            })),
        }
    }

    pub(crate) async fn snapshot(&self) -> RegistrationStatus {
        self.status.read().await.clone()
    }

    pub(crate) async fn start(
        &self,
        app: AppState,
        options: RegistrationOptions,
    ) -> Result<RegistrationStatus, String> {
        {
            let mut status = self.status.write().await;
            if status.state == "running" {
                return Err("已有注册任务正在运行".into());
            }
            status.state = "running".into();
            status.progress = 5;
            status.message = "正在启动浏览器注册器".into();
            status.email.clear();
        }
        let status_handle = self.status.clone();
        tokio::spawn(async move {
            let result = run_registration(&app, &options, &status_handle).await;
            let mut status = status_handle.write().await;
            match result {
                Ok(account) => {
                    status.state = "completed".into();
                    status.progress = 100;
                    status.email = account.email;
                    status.message = "注册成功，账号已加入账号池".into();
                }
                Err(error) => {
                    status.state = "failed".into();
                    status.progress = 100;
                    status.message = error;
                }
            }
        });
        Ok(self.snapshot().await)
    }
}

async fn set_progress(status: &Arc<RwLock<RegistrationStatus>>, progress: u8, message: &str) {
    let mut current = status.write().await;
    current.progress = progress;
    current.message = message.to_string();
}

async fn run_registration(
    app: &AppState,
    options: &RegistrationOptions,
    status: &Arc<RwLock<RegistrationStatus>>,
) -> Result<Account, String> {
    set_progress(status, 10, "正在启动 EmailMux 与 DeepSeek 页面").await;
    let configured_runner = std::env::var("DS_REGISTER_RUNNER").ok();
    let explicit_runner = configured_runner.is_some();
    let data_dir = std::env::var_os("DS_DATA_DIR")
        .map(std::path::PathBuf::from)
        .or_else(|| app.config_path.parent().map(std::path::Path::to_path_buf))
        .unwrap_or_else(|| std::env::current_dir().unwrap_or_default());
    let script = match std::env::var_os("DS_REGISTER_SCRIPT") {
        Some(script) => std::path::PathBuf::from(script),
        None => {
            let script_dir = data_dir.join(".ds-free-api");
            tokio::fs::create_dir_all(&script_dir)
                .await
                .map_err(|error| format!("无法准备注册器目录：{error}"))?;
            let script = script_dir.join("deepseek_register.py");
            tokio::fs::write(&script, REGISTRATION_SCRIPT)
                .await
                .map_err(|error| format!("无法写入内置注册器：{error}"))?;
            script
        }
    };
    let output_path = data_dir.join("registered_accounts.jsonl");

    let runners = configured_runner
        .map(|runner| vec![runner])
        .unwrap_or_else(|| vec!["uv".into(), "python".into(), "python3".into(), "py".into()]);
    let mut output = None;
    let mut selected_runner = String::new();
    for runner in runners {
        let mut command = Command::new(&runner);
        if is_runner(&runner, "uv") {
            command.args(["run", "--with", "playwright>=1.45,<2", "python"]);
        } else if is_runner(&runner, "py") {
            command.arg("-3");
        }
        command.env("PYTHONIOENCODING", "utf-8");
        if std::env::var_os("PLAYWRIGHT_BROWSERS_PATH").is_none()
            && let Ok(executable) = std::env::current_exe()
            && let Some(parent) = executable.parent()
        {
            let bundled_browsers = parent.join("playwright-browsers");
            if bundled_browsers.is_dir() {
                command.env("PLAYWRIGHT_BROWSERS_PATH", bundled_browsers);
            }
        }
        command.arg(&script).arg("--output").arg(&output_path);
        if options.headless {
            command.arg("--headless");
        }
        if let Some(url) = options.emailtick_url.as_deref() {
            command.args(["--emailtick-url", url]);
        }
        command.stdout(Stdio::piped()).stderr(Stdio::piped());
        match command.output().await {
            Ok(result) => {
                output = Some(result);
                selected_runner = runner;
                break;
            }
            Err(error) if error.kind() == std::io::ErrorKind::NotFound && !explicit_runner => {
                continue;
            }
            Err(error) => return Err(format!("无法启动注册器 {runner}: {error}")),
        }
    }
    let output = output.ok_or_else(|| {
        "找不到注册器运行环境，请安装 uv 或 Python 3.11+（并安装 Playwright）".to_string()
    })?;
    let stdout = String::from_utf8_lossy(&output.stdout);
    let stderr = String::from_utf8_lossy(&output.stderr);
    if !output.status.success() {
        let details = tail(&stderr);
        if details.contains("No module named") && details.contains("playwright") {
            return Err(
                "Python 缺少 Playwright，请执行：python -m pip install -r tools/requirements.txt"
                    .into(),
            );
        }
        return Err(format!("注册器退出失败（{selected_runner}）：{details}"));
    }
    set_progress(status, 80, "注册器完成，正在导入账号池").await;
    let account = stdout
        .lines()
        .rev()
        .find_map(|line| serde_json::from_str::<Account>(line.trim()).ok())
        .ok_or_else(|| format!("注册器未输出有效账号记录：{}", tail(&stdout)))?;

    let accounts = {
        let mut config = app.config.write().await;
        if config
            .ds_core
            .accounts
            .iter()
            .any(|existing| existing.email == account.email && !account.email.is_empty())
        {
            return Err(format!("账号已存在：{}", account.email));
        }
        config.ds_core.accounts.push(account.clone());
        config
            .save(&app.config_path)
            .map_err(|error| format!("保存账号配置失败：{error}"))?;
        config.ds_core.accounts.clone()
    };
    app.adapter.sync_accounts(&accounts).await;
    Ok(account)
}

fn is_runner(value: &str, name: &str) -> bool {
    Path::new(value)
        .file_stem()
        .and_then(|stem| stem.to_str())
        .is_some_and(|stem| stem.eq_ignore_ascii_case(name))
}

fn tail(value: &str) -> String {
    let value = value.trim();
    let chars: Vec<char> = value.chars().collect();
    if chars.len() > 800 {
        format!("…{}", chars[chars.len() - 800..].iter().collect::<String>())
    } else {
        value.to_string()
    }
}
