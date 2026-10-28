import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { listEmails } from "@/services/emails";
import { Card } from "@/components/cards/Card";
import { DataTable, Column } from "@/components/tables/DataTable";
import { StatusBadge } from "@/components/badges/StatusBadge";
import { Select } from "@/components/common/Input";
import { Pagination } from "@/components/common/Pagination";
import { formatDateTime } from "@/utils/format";
import type { EmailLog } from "@/types/api";

const LIMIT = 20;
const STATUS_OPTIONS = [
  { value: "sent", label: "Sent" },
  { value: "pending", label: "Pending" },
  { value: "failed", label: "Failed" },
  { value: "retrying", label: "Retrying" },
];

export function Emails() {
  const [status, setStatus] = useState("");
  const [offset, setOffset] = useState(0);

  const emails = useQuery({
    queryKey: ["emails", status, offset],
    queryFn: () => listEmails({ status: status || undefined, limit: LIMIT, offset }),
  });

  const columns: Column<EmailLog>[] = [
    {
      key: "lead",
      header: "Call/Lead",
      render: (e) => e.lead?.name ?? e.lead?.phone ?? "—",
    },
    { key: "recipient", header: "Recipient", render: (e) => e.recipient_email },
    { key: "department", header: "Department", render: (e) => e.lead?.department_name ?? "—" },
    { key: "sent_at", header: "Sent At", render: (e) => formatDateTime(e.sent_at) },
    { key: "status", header: "Status", render: (e) => <StatusBadge status={e.status} /> },
    { key: "retry", header: "Retry Count", render: (e) => e.retry_count },
    {
      key: "error",
      header: "Error",
      render: (e) => (e.error_message ? <span className="text-danger">{e.error_message}</span> : "—"),
    },
  ];

  return (
    <div className="space-y-4">
      <Card>
        <Select
          placeholder="All statuses"
          options={STATUS_OPTIONS}
          value={status}
          onChange={(e) => {
            setStatus(e.target.value);
            setOffset(0);
          }}
          className="max-w-xs"
        />
      </Card>

      <Card padded={false}>
        <DataTable<EmailLog>
          columns={columns}
          rows={emails.data?.items ?? []}
          keyExtractor={(e) => e.id}
          isLoading={emails.isLoading}
          isError={emails.isError}
          onRetry={() => emails.refetch()}
          emptyTitle="No emails sent yet"
          emptyDescription="Lead-notification emails will appear here once calls start generating leads."
        />
        {emails.data && (
          <Pagination total={emails.data.total} limit={LIMIT} offset={offset} onOffsetChange={setOffset} />
        )}
      </Card>
    </div>
  );
}
