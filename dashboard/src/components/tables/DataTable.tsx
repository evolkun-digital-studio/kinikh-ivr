import { ReactNode } from "react";
import { EmptyState } from "@/components/common/EmptyState";
import { ErrorState } from "@/components/common/ErrorState";
import { TableSkeleton } from "@/components/common/Skeleton";

export interface Column<T> {
  key: string;
  header: string;
  render: (row: T) => ReactNode;
  className?: string;
  sortKey?: string;
}

export function DataTable<T>({
  columns,
  rows,
  keyExtractor,
  isLoading,
  isError,
  onRetry,
  emptyTitle,
  emptyDescription,
  onRowClick,
  sortKey,
  sortOrder,
  onSortChange,
}: {
  columns: Column<T>[];
  rows: T[];
  keyExtractor: (row: T) => string;
  isLoading?: boolean;
  isError?: boolean;
  onRetry?: () => void;
  emptyTitle: string;
  emptyDescription?: string;
  onRowClick?: (row: T) => void;
  sortKey?: string;
  sortOrder?: "asc" | "desc";
  onSortChange?: (key: string) => void;
}) {
  if (isLoading) return <TableSkeleton cols={columns.length} />;
  if (isError) return <ErrorState onRetry={onRetry} />;
  if (rows.length === 0) return <EmptyState title={emptyTitle} description={emptyDescription} />;

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[640px] text-left text-sm">
        <thead>
          <tr className="border-b border-border text-xs uppercase tracking-wide text-ink-faint">
            {columns.map((col) => (
              <th key={col.key} className={`px-4 py-2.5 font-medium ${col.className ?? ""}`}>
                {col.sortKey ? (
                  <button
                    className="focus-ring flex items-center gap-1 hover:text-ink-muted"
                    onClick={() => onSortChange?.(col.sortKey!)}
                  >
                    {col.header}
                    {sortKey === col.sortKey && <span>{sortOrder === "asc" ? "↑" : "↓"}</span>}
                  </button>
                ) : (
                  col.header
                )}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {rows.map((row) => (
            <tr
              key={keyExtractor(row)}
              onClick={() => onRowClick?.(row)}
              className={onRowClick ? "cursor-pointer hover:bg-neutral-50" : ""}
            >
              {columns.map((col) => (
                <td key={col.key} className={`px-4 py-3 align-middle text-ink ${col.className ?? ""}`}>
                  {col.render(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
