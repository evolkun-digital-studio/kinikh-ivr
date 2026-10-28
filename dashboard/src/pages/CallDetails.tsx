import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { getCall } from "@/services/calls";
import { Card } from "@/components/cards/Card";
import { StatusBadge } from "@/components/badges/StatusBadge";
import { ErrorState } from "@/components/common/ErrorState";
import { Skeleton } from "@/components/common/Skeleton";
import { EmptyState } from "@/components/common/EmptyState";
import { AudioPlayer } from "@/components/common/AudioPlayer";
import { TranscriptView } from "@/components/common/TranscriptView";
import { formatCurrency, formatDateTime, formatDuration, languageLabel } from "@/utils/format";

function Field({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <p className="text-xs font-medium uppercase tracking-wide text-ink-faint">{label}</p>
      <p className="mt-0.5 text-sm text-ink">{value}</p>
    </div>
  );
}

export function CallDetails() {
  const { id } = useParams<{ id: string }>();
  const { data: call, isLoading, isError, refetch } = useQuery({
    queryKey: ["call", id],
    queryFn: () => getCall(id!),
    enabled: Boolean(id),
  });

  if (isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-40 w-full" />
        <Skeleton className="h-56 w-full" />
      </div>
    );
  }
  if (isError || !call) return <ErrorState onRetry={() => refetch()} />;

  return (
    <div className="space-y-4">
      <Link to="/calls" className="text-sm font-medium text-accent hover:underline">
        ← Back to calls
      </Link>

      <Card title="Call Information">
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
          <Field label="Caller" value={call.caller_number} />
          <Field label="Call SID" value={<span className="font-mono text-xs">{call.call_sid}</span>} />
          <Field label="Start Time" value={formatDateTime(call.started_at)} />
          <Field label="End Time" value={formatDateTime(call.ended_at)} />
          <Field label="Duration" value={formatDuration(call.duration_seconds)} />
          <Field label="Department" value={call.lead?.department_name ?? "—"} />
          <Field label="Language" value={languageLabel(call.lead?.language)} />
          <Field label="Status" value={<StatusBadge status={call.status} />} />
        </div>
        {call.error_message && (
          <p className="mt-4 rounded-md bg-danger-soft px-3 py-2 text-sm text-danger">
            {call.error_message}
          </p>
        )}
      </Card>

      <Card title="Conversation">
        {call.transcript ? (
          <TranscriptView transcript={call.transcript} />
        ) : (
          <EmptyState title="No transcript available" />
        )}
      </Card>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card title="Lead">
          {call.lead ? (
            <div className="space-y-3">
              <Field label="Name" value={call.lead.name ?? "—"} />
              <Field label="Phone" value={call.lead.phone} />
              <Link
                to={`/leads/${call.lead.id}`}
                className="inline-block text-sm font-medium text-accent hover:underline"
              >
                View full lead details →
              </Link>
            </div>
          ) : (
            <EmptyState title="No lead captured for this call" />
          )}
        </Card>

        <Card title="Recording">
          {call.recording?.has_audio ? (
            <AudioPlayer recordingId={call.recording.id} />
          ) : (
            <EmptyState title="No recording available" />
          )}
        </Card>
      </div>

      <Card title="Cost">
        {call.cost ? (
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-5">
            <Field label="Telephony" value={formatCurrency(call.cost.telephony_cost_usd)} />
            <Field label="OpenAI (AI)" value={formatCurrency(call.cost.openai_cost_usd)} />
            <Field label="Voice" value={formatCurrency(call.cost.voice_cost_usd)} />
            <Field label="Email" value={formatCurrency(call.cost.email_cost_usd)} />
            <Field label="Total" value={<span className="font-semibold">{formatCurrency(call.cost.total_cost_usd)}</span>} />
          </div>
        ) : (
          <EmptyState title="No cost data recorded for this call" />
        )}
      </Card>
    </div>
  );
}
