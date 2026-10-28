import { Card } from "@/components/cards/Card";
import { EmptyState } from "@/components/common/EmptyState";
import { Skeleton } from "@/components/common/Skeleton";
import { STATUS } from "@/components/charts/colors";
import { titleCase } from "@/utils/format";

const STATUS_COLOR: Record<string, string> = {
  completed: STATUS.good,
  sent: STATUS.good,
  failed: STATUS.critical,
  pending: STATUS.warning,
  retrying: STATUS.serious,
};

export function StatusBreakdownChart({
  title,
  data,
  isLoading,
  emptyLabel,
}: {
  title: string;
  data: Record<string, number>;
  isLoading?: boolean;
  emptyLabel: string;
}) {
  const entries = Object.entries(data);
  const total = entries.reduce((sum, [, count]) => sum + count, 0);

  return (
    <Card title={title}>
      {isLoading ? (
        <Skeleton className="h-32 w-full" />
      ) : total === 0 ? (
        <EmptyState title={emptyLabel} />
      ) : (
        <div className="space-y-3">
          <div className="flex h-3 w-full overflow-hidden rounded-full bg-neutral-100">
            {entries.map(([key, count]) => (
              <div
                key={key}
                style={{
                  width: `${(count / total) * 100}%`,
                  backgroundColor: STATUS_COLOR[key] ?? STATUS.warning,
                }}
                title={`${titleCase(key)}: ${count}`}
              />
            ))}
          </div>
          <ul className="flex flex-wrap gap-x-5 gap-y-2">
            {entries.map(([key, count]) => (
              <li key={key} className="flex items-center gap-2 text-sm">
                <span
                  className="h-2.5 w-2.5 rounded-full"
                  style={{ backgroundColor: STATUS_COLOR[key] ?? STATUS.warning }}
                />
                <span className="text-ink-muted">{titleCase(key)}</span>
                <span className="font-medium text-ink tabular-nums">{count}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </Card>
  );
}
