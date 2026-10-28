import { titleCase } from "@/utils/format";

type Tone = "neutral" | "success" | "warning" | "danger" | "accent";

const toneClasses: Record<Tone, string> = {
  neutral: "bg-neutral-100 text-ink-muted",
  success: "bg-success-soft text-success",
  warning: "bg-warning-soft text-warning",
  danger: "bg-danger-soft text-danger",
  accent: "bg-accent-soft text-accent-hover",
};

const STATUS_TONE: Record<string, Tone> = {
  completed: "success",
  connected: "success",
  sent: "success",
  active: "success",
  in_progress: "accent",
  ringing: "accent",
  initiated: "accent",
  retrying: "warning",
  pending: "warning",
  configuration_pending: "warning",
  not_verified: "warning",
  failed: "danger",
  error: "danger",
  inactive: "neutral",
  not_configured: "neutral",
};

export function StatusBadge({ status }: { status: string | null | undefined }) {
  if (!status) return <span className="text-ink-faint">—</span>;
  const tone = STATUS_TONE[status.toLowerCase()] ?? "neutral";
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium ${toneClasses[tone]}`}
    >
      <span
        className={`h-1.5 w-1.5 rounded-full ${
          tone === "success"
            ? "bg-success"
            : tone === "warning"
              ? "bg-warning"
              : tone === "danger"
                ? "bg-danger"
                : tone === "accent"
                  ? "bg-accent"
                  : "bg-ink-faint"
        }`}
      />
      {titleCase(status)}
    </span>
  );
}
