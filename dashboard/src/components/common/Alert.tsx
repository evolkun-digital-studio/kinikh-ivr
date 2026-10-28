import { ReactNode } from "react";

type Tone = "info" | "success" | "warning" | "danger";

const toneClasses: Record<Tone, string> = {
  info: "bg-accent-soft border-accent/20 text-accent-hover",
  success: "bg-success-soft border-success/20 text-success",
  warning: "bg-warning-soft border-warning/20 text-warning",
  danger: "bg-danger-soft border-danger/20 text-danger",
};

export function Alert({ tone = "info", title, children }: { tone?: Tone; title?: string; children: ReactNode }) {
  return (
    <div className={`rounded-md border px-4 py-3 text-sm ${toneClasses[tone]}`}>
      {title && <p className="font-medium">{title}</p>}
      <div className={title ? "mt-1 text-ink-muted" : "text-ink-muted"}>{children}</div>
    </div>
  );
}
