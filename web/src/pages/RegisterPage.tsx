import { useCallback, useEffect, useRef, useState } from 'react';
import { Play, RefreshCw, UserPlus } from 'lucide-react';
import { apiFetchRegistrationStatus, apiStartRegistration } from '@/lib/api';
import type { RegistrationStatus } from '@/lib/api';

const DEFAULT_EMAIL_URL = 'https://emailmux.com/tw/temporary-gmail';

export function RegisterPage() {
  const [emailUrl, setEmailUrl] = useState(DEFAULT_EMAIL_URL);
  const [headless, setHeadless] = useState(false);
  const [status, setStatus] = useState<RegistrationStatus | null>(null);
  const [starting, setStarting] = useState(false);
  const timer = useRef<number | undefined>(undefined);

  const refresh = useCallback(async () => {
    try {
      const next = await apiFetchRegistrationStatus();
      setStatus(next);
      if (next.state !== 'running' && timer.current !== undefined) {
        window.clearInterval(timer.current);
        timer.current = undefined;
      }
    } catch {
      // 登录状态由 apiFetch 统一处理。
    }
  }, []);

  useEffect(() => {
    void refresh();
    return () => {
      if (timer.current !== undefined) window.clearInterval(timer.current);
    };
  }, [refresh]);

  const start = async () => {
    setStarting(true);
    try {
      const next = await apiStartRegistration({ emailtick_url: emailUrl || undefined, headless });
      setStatus(next);
      if (timer.current !== undefined) window.clearInterval(timer.current);
      timer.current = window.setInterval(() => void refresh(), 2000);
    } finally {
      setStarting(false);
    }
  };

  const running = status?.state === 'running';
  const failed = status?.state === 'failed';
  const completed = status?.state === 'completed';

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight flex items-center gap-2"><UserPlus className="h-6 w-6" />自动注册</h1>
        <p className="text-muted-foreground mt-1">使用 EmailMux 获取临时邮箱，注册成功后自动加入 DeepSeek 账号池。</p>
      </div>

      <section className="rounded-xl border bg-card p-5 space-y-5">
        <div className="space-y-2">
          <label htmlFor="email-url" className="text-sm font-medium">邮箱站点</label>
          <input id="email-url" value={emailUrl} onChange={(event) => setEmailUrl(event.target.value)} disabled={running}
            className="w-full rounded-md border bg-background px-3 py-2 text-sm" />
          <p className="text-xs text-muted-foreground">默认使用 EmailMux；需要其他邮箱页面时可替换此 URL。</p>
        </div>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={headless} onChange={(event) => setHeadless(event.target.checked)} disabled={running} />
          无头模式（遇到 Cloudflare/CAPTCHA 时请关闭）
        </label>
        <button type="button" onClick={() => void start()} disabled={running || starting}
          className="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50">
          {running ? <RefreshCw className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
          {running ? '注册进行中…' : starting ? '启动中…' : '开始注册'}
        </button>
      </section>

      <section className="rounded-xl border bg-card p-5 space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="font-semibold">任务状态</h2>
          <button type="button" onClick={() => void refresh()} className="text-sm text-muted-foreground hover:text-foreground">刷新</button>
        </div>
        <div className="h-3 overflow-hidden rounded-full bg-muted"><div className="h-full bg-primary transition-all" style={{ width: `${status?.progress ?? 0}%` }} /></div>
        <div className="text-sm text-muted-foreground">{status?.message ?? '尚未开始注册'}</div>
        {status?.email && <div className="text-sm">邮箱：<code>{status.email}</code></div>}
        {completed && <div className="rounded-md bg-green-500/10 px-3 py-2 text-sm text-green-700 dark:text-green-300">账号已保存并热加入账号池。</div>}
        {failed && <div className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">注册失败，请查看上方错误信息或运行日志。</div>}
      </section>
    </div>
  );
}
