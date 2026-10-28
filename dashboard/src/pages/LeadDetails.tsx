import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { getLead } from "@/services/leads";
import { Card } from "@/components/cards/Card";
import { StatusBadge } from "@/components/badges/StatusBadge";
import { ErrorState } from "@/components/common/ErrorState";
import { Skeleton } from "@/components/common/Skeleton";
import { EmptyState } from "@/components/common/EmptyState";
import { TranscriptView } from "@/components/common/TranscriptView";
import { formatDateTime, languageLabel } from "@/utils/format";

function Field({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <p className="text-xs font-medium uppercase tracking-wide text-ink-faint">{label}</p>
      <p className="mt-0.5 text-sm text-ink">{value}</p>
    </div>
  );
}

export function LeadDetails() {
  const { id } = useParams<{ id: string }>();
  const { data: lead, isLoading, isError, refetch } = useQuery({
    queryKey: ["lead", id],
    queryFn: () => getLead(id!),
    enabled: Boolean(id),
  });

  if (isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-40 w-full" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }
  if (isError || !lead) return <ErrorState onRetry={() => refetch()} />;

  return (
    <div className="space-y-4">
      <Link to="/leads" className="text-sm font-medium text-accent hover:underline">
        ← Back to leads
      </Link>

      <Card title="Caller Information">
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
          <Field label="Name" value={lead.name ?? "—"} />
          <Field label="Phone" value={lead.phone} />
          <Field label="Email" value={lead.email ?? "—"} />
          <Field label="Department" value={lead.department?.display_name ?? "—"} />
          <Field label="Language" value={languageLabel(lead.language)} />
          <Field label="Created" value={formatDateTime(lead.created_at)} />
        </div>
      </Card>

      <Card title="Extracted Information">
        <div className="space-y-3">
          <Field label="Requirement" value={lead.requirement ?? "—"} />
          <Field label="Summary" value={lead.summary ?? "—"} />
          <Field label="Additional Notes" value={lead.additional_notes ?? "—"} />
        </div>
      </Card>

      <Card title="Call Reference">
        {lead.call ? (
          <div className="flex items-center justify-between">
            <div className="space-y-1">
              <p className="font-mono text-xs text-ink-muted">{lead.call.call_sid}</p>
              <StatusBadge status={lead.call.status} />
            </div>
            <Link
              to={`/calls/${lead.call.id}`}
              className="text-sm font-medium text-accent hover:underline"
            >
              View call details →
            </Link>
          </div>
        ) : (
          <EmptyState title="No linked call" />
        )}
      </Card>

      <Card title="Transcript">
        {lead.raw_transcript ? (
          <TranscriptView transcript={lead.raw_transcript} />
        ) : (
          <EmptyState title="No transcript available" />
        )}
      </Card>

      <Card title="Email Delivery Status" padded={false}>
        {lead.email_logs.length === 0 ? (
          <div className="p-4">
            <EmptyState title="No emails sent for this lead" />
          </div>
        ) : (
          <ul className="divide-y divide-border">
            {lead.email_logs.map((log) => (
              <li key={log.id} className="flex items-center justify-between px-4 py-3 text-sm">
                <div>
                  <p className="text-ink">{log.recipient_email}</p>
                  <p className="text-xs text-ink-faint">
                    {log.sent_at ? formatDateTime(log.sent_at) : "Not yet sent"}
                    {log.retry_count > 0 ? ` · ${log.retry_count} retries` : ""}
                  </p>
                </div>
                <StatusBadge status={log.status} />
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
