import type { Health } from "../../types";

type StatusBarProps = {
  health: Health | null;
  error: string | null;
};

export function StatusBar({ health, error }: StatusBarProps) {
  const state = error ? "异常" : health?.status === "ok" ? "服务正常" : "连接中";
  return (
    <section className="status-bar" aria-live="polite" aria-label="系统状态">
      <div>
        <span className={`status-dot status-dot--${error ? "error" : health?.status ?? "loading"}`} />
        <strong>{state}</strong>
        <span>{error ?? `API ${health?.version ?? "—"}`}</span>
      </div>
      <div className="status-meta">
        <span>本地优先</span>
        <span>Point-in-Time</span>
        <span>只读研究</span>
      </div>
    </section>
  );
}
