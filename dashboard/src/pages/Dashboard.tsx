import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { getDashboardSummary } from "@/services/dashboard";
import { getAnalyticsOverview } from "@/services/analytics";
import { KpiCard } from "@/components/cards/KpiCard";
import { Card } from "@/components/cards/Card";
import { DataTable } from "@/components/tables/DataTable";
import { StatusBadge } from "@/components/badges/StatusBadge";
import { ErrorState } from "@/components/common/ErrorState";
import { TimeSeriesChart } from "@/components/charts/TimeSeriesChart";
import { CategoryBarChart } from "@/components/charts/CategoryBarChart";
import { formatCurrency, formatDateTime, formatDuration, languageLabel } from "@/utils/format";
import type { Call, Lead } from "@/types/api";

export function Dashboard() {
  const summary = useQuery({ queryKey: ["dashboard-summary"], queryFn: getDashboardSummary });
  const analytics = useQuery({ queryKey: ["analytics-overview", 30], queryFn: () => getAnalyticsOverview(30) });

  if (summary.isError) {
    return <ErrorState onRetry={() => summary.refetch()} />;
  }

  const kpis = summary.data?.kpis;

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <KpiCard label="Total Calls" value={String(kpis?.total_calls ?? 0)} isLoading={summary.isLoading} />
        <KpiCard
          label="Completed Calls"
          value={String(kpis?.completed_calls ?? 0)}
          isLoading={summary.isLoading}
        />
        <KpiCard label="Failed Calls" value={String(kpis?.failed_calls ?? 0)} isLoading={summary.isLoading} />
        <KpiCard label="Total Leads" value={String(kpis?.total_leads ?? 0)} isLoading={summary.isLoading} />
        <KpiCard label="Today's Calls" value={String(kpis?.today_calls ?? 0)} isLoading={summary.isLoading} />
        <KpiCard label="Today's Leads" value={String(kpis?.today_leads ?? 0)} isLoading={summary.isLoading} />
        <KpiCard
          label="Total API Cost"
          value={formatCurrency(kpis?.total_cost_usd)}
          isLoading={summary.isLoading}
        />
        <KpiCard
          label="Avg. Call Duration"
          value={formatDuration(kpis?.average_call_duration_seconds)}
          isLoading={summary.isLoading}
        />
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <TimeSeriesChart
          title="Call Activity"
          valueLabel="Calls"
          isLoading={analytics.isLoading}
          emptyLabel="No calls yet"
          data={(analytics.data?.calls_over_time ?? []).map((p) => ({ date: p.date, value: p.count }))}
        />
        <TimeSeriesChart
          title="Lead Activity"
          valueLabel="Leads"
          isLoading={analytics.isLoading}
          emptyLabel="No leads yet"
          data={(analytics.data?.leads_over_time ?? []).map((p) => ({ date: p.date, value: p.count }))}
        />
        <CategoryBarChart
          title="Calls by Department"
          isLoading={analytics.isLoading}
          emptyLabel="No department data yet"
          data={(analytics.data?.calls_by_department ?? []).map((d) => ({
            label: d.department_name ?? "Unassigned",
            value: d.count,
          }))}
        />
        <CategoryBarChart
          title="Calls by Language"
          isLoading={analytics.isLoading}
          emptyLabel="No language data yet"
          data={Object.entries(analytics.data?.calls_by_language ?? {}).map(([lang, count]) => ({
            label: languageLabel(lang),
            value: count,
          }))}
        />
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <Card title="Recent Calls" padded={false}>
          <DataTable<Call>
            columns={[
              { key: "caller", header: "Caller", render: (c) => c.caller_number },
              { key: "date", header: "Date/Time", render: (c) => formatDateTime(c.created_at) },
              { key: "duration", header: "Duration", render: (c) => formatDuration(c.duration_seconds) },
              {
                key: "department",
                header: "Department",
                render: (c) => c.lead?.department_name ?? "—",
              },
              {
                key: "language",
                header: "Language",
                render: (c) => languageLabel(c.lead?.language),
              },
              { key: "status", header: "Status", render: (c) => <StatusBadge status={c.status} /> },
              {
                key: "lead",
                header: "Lead",
                render: (c) => (c.lead ? <StatusBadge status="captured" /> : "—"),
              },
              {
                key: "action",
                header: "",
                render: (c) => (
                  <Link to={`/calls/${c.id}`} className="text-sm font-medium text-accent hover:underline">
                    View
                  </Link>
                ),
              },
            ]}
            rows={summary.data?.recent_calls ?? []}
            keyExtractor={(c) => c.id}
            isLoading={summary.isLoading}
            emptyTitle="No calls yet"
          />
        </Card>

        <Card title="Recent Leads" padded={false}>
          <DataTable<Lead>
            columns={[
              { key: "name", header: "Name", render: (l) => l.name ?? "—" },
              { key: "phone", header: "Phone", render: (l) => l.phone },
              { key: "department", header: "Department", render: (l) => l.department?.display_name ?? "—" },
              {
                key: "requirement",
                header: "Requirement",
                render: (l) => <span className="line-clamp-1 max-w-[16rem]">{l.requirement ?? "—"}</span>,
              },
              { key: "created", header: "Created", render: (l) => formatDateTime(l.created_at) },
              {
                key: "action",
                header: "",
                render: (l) => (
                  <Link to={`/leads/${l.id}`} className="text-sm font-medium text-accent hover:underline">
                    View
                  </Link>
                ),
              },
            ]}
            rows={summary.data?.recent_leads ?? []}
            keyExtractor={(l) => l.id}
            isLoading={summary.isLoading}
            emptyTitle="No leads yet"
          />
        </Card>
      </div>
    </div>
  );
}
