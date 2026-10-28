import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { listRecordings } from "@/services/recordings";
import { Card } from "@/components/cards/Card";
import { DataTable, Column } from "@/components/tables/DataTable";
import { StatusBadge } from "@/components/badges/StatusBadge";
import { Pagination } from "@/components/common/Pagination";
import { AudioPlayer } from "@/components/common/AudioPlayer";
import { Input } from "@/components/common/Input";
import { useDebounce } from "@/hooks/useDebounce";
import { formatDateTime, formatDuration } from "@/utils/format";
import type { Recording } from "@/types/api";

const LIMIT = 20;

export function Recordings() {
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const debouncedSearch = useDebounce(search);

  const recordings = useQuery({
    queryKey: ["recordings", debouncedSearch, offset],
    queryFn: () => listRecordings({ search: debouncedSearch || undefined, limit: LIMIT, offset }),
  });

  const columns: Column<Recording>[] = [
    { key: "call", header: "Call", render: (r) => <span className="font-mono text-xs">{r.call_sid}</span> },
    { key: "caller", header: "Caller", render: (r) => r.caller_number },
    { key: "department", header: "Department", render: (r) => r.department ?? "—" },
    { key: "duration", header: "Duration", render: (r) => formatDuration(r.duration) },
    { key: "date", header: "Date", render: (r) => formatDateTime(r.start_time) },
    { key: "status", header: "Status", render: (r) => <StatusBadge status={r.status} /> },
    {
      key: "audio",
      header: "Recording",
      render: (r) =>
        r.has_audio ? (
          <div className="min-w-[220px]">
            <AudioPlayer recordingId={r.id} />
          </div>
        ) : (
          <span className="text-sm text-ink-faint">No recording</span>
        ),
    },
  ];

  return (
    <div className="space-y-4">
      <Card>
        <Input
          placeholder="Search caller…"
          value={search}
          onChange={(e) => {
            setSearch(e.target.value);
            setOffset(0);
          }}
          className="max-w-xs"
        />
      </Card>

      <Card padded={false}>
        <DataTable<Recording>
          columns={columns}
          rows={recordings.data?.items ?? []}
          keyExtractor={(r) => r.id}
          isLoading={recordings.isLoading}
          isError={recordings.isError}
          onRetry={() => recordings.refetch()}
          emptyTitle="No recordings yet"
          emptyDescription="Call recordings will appear here once calls complete."
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
    </div>
  );
}
