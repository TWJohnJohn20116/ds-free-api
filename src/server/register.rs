//! 后台自动注册任务：执行 Playwright 注册器并将成功账号热加入账号池。

use std::process::Stdio;
use std::sync::Arc;

use serde::{Deserialize, Serialize};
use tokio::process::Command;
use tokio::sync::RwLock;

use crate::config::Account;

use super::handlers::AppState;

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
    let runner = std::env::var("DS_REGISTER_RUNNER").unwrap_or_else(|_| "uv".into());
    let script =
        std::env::var("DS_REGISTER_SCRIPT").unwrap_or_else(|_| "tools/deepseek_register.py".into());
    let output_path = std::env::var("DS_DATA_DIR")
        .map(|dir| format!("{dir}/registered_accounts.jsonl"))
        .unwrap_or_else(|_| "registered_accounts.jsonl".into());

    let mut command = Command::new(&runner);
    if runner.eq_ignore_ascii_case("uv") {
        command.args(["run", "--with", "playwright>=1.45,<2", "python"]);
    }
    command.arg(&script).args(["--output", &output_path]);
    if options.headless {
        command.arg("--headless");
    }
    if let Some(url) = options.emailtick_url.as_deref() {
        command.args(["--emailtick-url", url]);
    }
    command.stdout(Stdio::piped()).stderr(Stdio::piped());

    let output = command
        .output()
        .await
        .map_err(|error| format!("无法启动注册器 {runner}: {error}"))?;
    let stdout = String::from_utf8_lossy(&output.stdout);
    let stderr = String::from_utf8_lossy(&output.stderr);
    if !output.status.success() {
        return Err(format!("注册器退出失败：{}", tail(&stderr)));
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

fn tail(value: &str) -> String {
    let value = value.trim();
    let chars: Vec<char> = value.chars().collect();
    if chars.len() > 800 {
        format!("…{}", chars[chars.len() - 800..].iter().collect::<String>())
    } else {
        value.to_string()
    }
}
