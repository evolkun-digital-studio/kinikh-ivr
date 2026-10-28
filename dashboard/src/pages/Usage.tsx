import { useQuery } from "@tanstack/react-query";
import { getUsageSummary, getUsageTimeseries } from "@/services/usage";
import { getSystemStatus } from "@/services/system";
import { KpiCard } from "@/components/cards/KpiCard";
import { Card } from "@/components/cards/Card";
import { StatusBadge } from "@/components/badges/StatusBadge";
import { TimeSeriesChart } from "@/components/charts/TimeSeriesChart";
import { EmptyState } from "@/components/common/EmptyState";
import { ErrorState } from "@/components/common/ErrorState";
import { formatCurrency, formatNumber, titleCase } from "@/utils/format";

export function Usage() {
  const summary = useQuery({ queryKey: ["usage-summary"], queryFn: getUsageSummary });
  const timeseries = useQuery({ queryKey: ["usage-timeseries", 30], queryFn: () => getUsageTimeseries(30) });
  const status = useQuery({ queryKey: ["system-status"], queryFn: getSystemStatus });

  if (summary.isError) return <ErrorState onRetry={() => summary.refetch()} />;

  const totals = summary.data?.totals;

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <KpiCard label="Total Cost" value={formatCurrency(totals?.total_usd)} isLoading={summary.isLoading} />
        <KpiCard label="OpenAI Cost" value={formatCurrency(totals?.openai_usd)} isLoading={summary.isLoading} />
        <KpiCard label="Voice Cost" value={formatCurrency(totals?.voice_usd)} isLoading={summary.isLoading} />
        <KpiCard
          label="Telephony Cost"
          value={formatCurrency(totals?.telephony_usd)}
          isLoading={summary.isLoading}
        />
        <KpiCard label="Email Cost" value={formatCurrency(totals?.email_usd)} isLoading={summary.isLoading} />
        <KpiCard
          label="Cost per Call"
          value={formatCurrency(totals?.cost_per_call_usd)}
          isLoading={summary.isLoading}
        />
        <KpiCard label="Total Calls Costed" value={formatNumber(totals?.call_count)} isLoading={summary.isLoading} />
      </div>

      <TimeSeriesChart
        title="Cost Over Time"
        valueLabel="Cost"
        formatValue={(v) => formatCurrency(v)}
        isLoading={timeseries.isLoading}
        emptyLabel="No cost data yet"
        data={(timeseries.data ?? []).map((p) => ({ date: p.date, value: p.cost_usd }))}
      />

      <Card title="API Requests & Token Usage" padded={false}>
        {summary.isLoading ? (
          <div className="p-4">
            <EmptyState title="Loading…" />
          </div>
        ) : (summary.data?.by_provider.length ?? 0) === 0 ? (
          <div className="p-4">
            <EmptyState title="No API usage recorded yet" />
          </div>
        ) : (
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-border text-xs uppercase tracking-wide text-ink-faint">
                <th className="px-4 py-2.5 font-medium">Provider</th>
                <th className="px-4 py-2.5 font-medium">Requests</th>
                <th className="px-4 py-2.5 font-medium">Tokens</th>
                <th className="px-4 py-2.5 font-medium">Voice Characters</th>
                <th className="px-4 py-2.5 font-medium">Est. Cost</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {summary.data?.by_provider.map((row) => (
                <tr key={row.provider}>
                  <td className="px-4 py-3">{titleCase(row.provider)}</td>
                  <td className="px-4 py-3 tabular-nums">{formatNumber(row.requests)}</td>
                  <td className="px-4 py-3 tabular-nums">{formatNumber(row.total_tokens)}</td>
                  <td className="px-4 py-3 tabular-nums">{formatNumber(row.total_characters)}</td>
                  <td className="px-4 py-3 tabular-nums">{formatCurrency(row.estimated_cost_usd, 6)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <Card title="Provider Configuration Status">
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          {(status.data?.providers ?? []).map((p) => (
            <div key={p.provider} className="rounded-md border border-border p-3">
              <p className="text-sm font-medium capitalize text-ink">{p.provider}</p>
              <div className="mt-1.5">
                <StatusBadge status={p.status} />
              </div>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
