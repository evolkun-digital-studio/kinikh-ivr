import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { getAnalyticsOverview } from "@/services/analytics";
import { Select } from "@/components/common/Input";
import { TimeSeriesChart } from "@/components/charts/TimeSeriesChart";
import { CategoryBarChart } from "@/components/charts/CategoryBarChart";
import { StatusBreakdownChart } from "@/components/charts/StatusBreakdownChart";
import { ErrorState } from "@/components/common/ErrorState";
import { formatCurrency, languageLabel } from "@/utils/format";

const RANGE_OPTIONS = [
  { value: "7", label: "Last 7 days" },
  { value: "30", label: "Last 30 days" },
  { value: "90", label: "Last 90 days" },
];

export function Analytics() {
  const [days, setDays] = useState(30);
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["analytics-overview", days],
    queryFn: () => getAnalyticsOverview(days),
  });

  if (isError) return <ErrorState onRetry={() => refetch()} />;

  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <Select
          options={RANGE_OPTIONS}
          value={String(days)}
          onChange={(e) => setDays(Number(e.target.value))}
          className="w-40"
        />
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <TimeSeriesChart
          title="Calls Over Time"
          valueLabel="Calls"
          isLoading={isLoading}
          emptyLabel="No calls yet"
          data={(data?.calls_over_time ?? []).map((p) => ({ date: p.date, value: p.count }))}
        />
        <TimeSeriesChart
          title="Leads Over Time"
          valueLabel="Leads"
          isLoading={isLoading}
          emptyLabel="No leads yet"
          data={(data?.leads_over_time ?? []).map((p) => ({ date: p.date, value: p.count }))}
        />
        <CategoryBarChart
          title="Calls by Department"
          isLoading={isLoading}
          emptyLabel="No department data yet"
          data={(data?.calls_by_department ?? []).map((d) => ({
            label: d.department_name ?? "Unassigned",
            value: d.count,
          }))}
        />
        <CategoryBarChart
          title="Calls by Language"
          isLoading={isLoading}
          emptyLabel="No language data yet"
          data={Object.entries(data?.calls_by_language ?? {}).map(([lang, count]) => ({
            label: languageLabel(lang),
            value: count,
          }))}
        />
        <StatusBreakdownChart
          title="Completed vs Failed Calls"
          isLoading={isLoading}
          emptyLabel="No completed or failed calls yet"
          data={data?.completed_vs_failed ?? {}}
        />
        <StatusBreakdownChart
          title="Email Delivery Success/Failure"
          isLoading={isLoading}
          emptyLabel="No emails sent yet"
          data={data?.email_status_breakdown ?? {}}
        />
        <TimeSeriesChart
          title="API Cost Over Time"
          valueLabel="Cost"
          formatValue={(v) => formatCurrency(v)}
          isLoading={isLoading}
          emptyLabel="No cost data yet"
          data={(data?.cost_over_time ?? []).map((p) => ({ date: p.date, value: p.cost_usd }))}
        />
      </div>
    </div>
  );
}
