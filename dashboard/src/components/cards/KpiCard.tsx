import { ReactNode } from "react";
import { CardSkeleton } from "@/components/common/Skeleton";

export function KpiCard({
  label,
  value,
  hint,
  icon,
  isLoading,
}: {
  label: string;
  value: string;
  hint?: string;
  icon?: ReactNode;
  isLoading?: boolean;
}) {
  if (isLoading) return <CardSkeleton />;

  return (
    <div className="rounded-lg border border-border bg-white p-4 shadow-card">
      <div className="flex items-center justify-between">
        <p className="text-xs font-medium uppercase tracking-wide text-ink-muted">{label}</p>
        {icon && <span className="text-ink-faint">{icon}</span>}
      </div>
      <p className="mt-2 text-2xl font-semibold tabular-nums text-ink">{value}</p>
      {hint && <p className="mt-1 text-xs text-ink-faint">{hint}</p>}
    </div>
  );
}
