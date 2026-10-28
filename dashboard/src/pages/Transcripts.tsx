import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { getRecording, listRecordings } from "@/services/recordings";
import { Card } from "@/components/cards/Card";
import { DataTable, Column } from "@/components/tables/DataTable";
import { Pagination } from "@/components/common/Pagination";
import { Drawer } from "@/components/modals/Drawer";
import { EmptyState } from "@/components/common/EmptyState";
import { Skeleton } from "@/components/common/Skeleton";
import { TranscriptView } from "@/components/common/TranscriptView";
import { formatDateTime, languageLabel } from "@/utils/format";
import type { Recording } from "@/types/api";

const LIMIT = 20;

function TranscriptDrawerContent({ id }: { id: string }) {
  const { data, isLoading } = useQuery({ queryKey: ["recording", id], queryFn: () => getRecording(id) });

  if (isLoading) return <Skeleton className="h-64 w-full" />;
  if (!data) return null;

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-3 text-sm">
        <div>
          <p className="text-xs font-medium uppercase tracking-wide text-ink-faint">Caller</p>
          <p className="mt-0.5 text-ink">{data.caller_number}</p>
        </div>
        <div>
          <p className="text-xs font-medium uppercase tracking-wide text-ink-faint">Call ID</p>
          <p className="mt-0.5 font-mono text-xs text-ink">{data.call_sid}</p>
        </div>
        <div>
          <p className="text-xs font-medium uppercase tracking-wide text-ink-faint">Language</p>
          <p className="mt-0.5 text-ink">{languageLabel(data.language)}</p>
        </div>
        <div>
          <p className="text-xs font-medium uppercase tracking-wide text-ink-faint">Timestamp</p>
          <p className="mt-0.5 text-ink">{formatDateTime(data.start_time)}</p>
        </div>
      </div>

      <div>
        <p className="mb-2 text-xs font-medium uppercase tracking-wide text-ink-faint">AI Summary</p>
        {data.summary ? (
          <p className="text-sm text-ink">{data.summary}</p>
        ) : (
          <p className="text-sm text-ink-faint">No summary generated</p>
        )}
      </div>

      <div>
        <p className="mb-2 text-xs font-medium uppercase tracking-wide text-ink-faint">Transcript</p>
        {data.transcript ? (
          <TranscriptView transcript={data.transcript} />
        ) : (
          <p className="text-sm text-ink-faint">No transcript available</p>
        )}
      </div>
    </div>
  );
}

export function Transcripts() {
  const [offset, setOffset] = useState(0);
  const [activeId, setActiveId] = useState<string | null>(null);

  const recordings = useQuery({
    queryKey: ["transcripts", offset],
    queryFn: () => listRecordings({ limit: LIMIT, offset }),
  });

  const columns: Column<Recording>[] = [
    { key: "caller", header: "Caller", render: (r) => r.caller_number },
    { key: "call_id", header: "Call ID", render: (r) => <span className="font-mono text-xs">{r.call_sid}</span> },
    { key: "language", header: "Language", render: (r) => languageLabel(r.language) },
    { key: "timestamp", header: "Timestamp", render: (r) => formatDateTime(r.start_time) },
  ];

  return (
    <div className="space-y-4">
      <Card padded={false}>
        <DataTable<Recording>
          columns={columns}
          rows={recordings.data?.items ?? []}
          keyExtractor={(r) => r.id}
          isLoading={recordings.isLoading}
          isError={recordings.isError}
          onRetry={() => recordings.refetch()}
          onRowClick={(r) => setActiveId(r.id)}
          emptyTitle="No transcripts yet"
          emptyDescription="Conversation transcripts appear here after calls complete."
        />
        {recordings.data && (
          <Pagination
            total={recordings.data.total}
            limit={LIMIT}
            offset={offset}
            onOffsetChange={setOffset}
          />
        )}
      </Card>

      <Drawer isOpen={Boolean(activeId)} onClose={() => setActiveId(null)} title="Transcript">
        {activeId ? <TranscriptDrawerContent id={activeId} /> : <EmptyState title="Select a call" />}
      </Drawer>
    </div>
  );
}
