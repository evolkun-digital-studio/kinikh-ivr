import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { listCalls } from "@/services/calls";
import { listDepartments } from "@/services/departments";
import { Card } from "@/components/cards/Card";
import { DataTable, Column } from "@/components/tables/DataTable";
import { StatusBadge } from "@/components/badges/StatusBadge";
import { Input, Select } from "@/components/common/Input";
import { Pagination } from "@/components/common/Pagination";
import { useDebounce } from "@/hooks/useDebounce";
import { formatDateTime, formatDuration, languageLabel } from "@/utils/format";
import type { Call } from "@/types/api";

const LIMIT = 20;
const STATUS_OPTIONS = [
  { value: "initiated", label: "Initiated" },
  { value: "ringing", label: "Ringing" },
  { value: "in_progress", label: "In progress" },
  { value: "completed", label: "Completed" },
  { value: "failed", label: "Failed" },
];
const LANGUAGE_OPTIONS = [
  { value: "en", label: "English" },
  { value: "hi", label: "Hindi" },
];

export function Calls() {
  const navigate = useNavigate();
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const [departmentId, setDepartmentId] = useState("");
  const [language, setLanguage] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [sort, setSort] = useState("created_at");
  const [order, setOrder] = useState<"asc" | "desc">("desc");
  const [offset, setOffset] = useState(0);

  const debouncedSearch = useDebounce(search);
  const departments = useQuery({ queryKey: ["departments"], queryFn: listDepartments });

  const filters = {
    search: debouncedSearch || undefined,
    status: status || undefined,
    department_id: departmentId || undefined,
    language: language || undefined,
    date_from: dateFrom || undefined,
    date_to: dateTo || undefined,
    sort,
    order,
    limit: LIMIT,
    offset,
  };

  const calls = useQuery({
    queryKey: ["calls", filters],
    queryFn: () => listCalls(filters),
  });

  const toggleSort = (key: string) => {
    if (sort === key) {
      setOrder(order === "asc" ? "desc" : "asc");
    } else {
      setSort(key);
      setOrder("desc");
    }
    setOffset(0);
  };

  const columns: Column<Call>[] = [
    { key: "caller", header: "Caller", sortKey: "caller_number", render: (c) => c.caller_number },
    {
      key: "created_at",
      header: "Date & Time",
      sortKey: "created_at",
      render: (c) => formatDateTime(c.created_at),
    },
    {
      key: "duration_seconds",
      header: "Duration",
      sortKey: "duration_seconds",
      render: (c) => formatDuration(c.duration_seconds),
    },
    { key: "department", header: "Department", render: (c) => c.lead?.department_name ?? "—" },
    { key: "language", header: "Language", render: (c) => languageLabel(c.lead?.language) },
    { key: "status", header: "Status", sortKey: "status", render: (c) => <StatusBadge status={c.status} /> },
    { key: "lead", header: "Lead", render: (c) => (c.lead ? <StatusBadge status="captured" /> : "—") },
    {
      key: "action",
      header: "",
      render: (c) => (
        <button
          onClick={(e) => {
            e.stopPropagation();
            navigate(`/calls/${c.id}`);
          }}
          className="text-sm font-medium text-accent hover:underline"
        >
          View
        </button>
      ),
    },
  ];

  return (
    <div className="space-y-4">
      <Card>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
          <Input
            placeholder="Search caller…"
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setOffset(0);
            }}
            className="col-span-2 sm:col-span-1"
          />
          <Select
            placeholder="All statuses"
            options={STATUS_OPTIONS}
            value={status}
            onChange={(e) => {
              setStatus(e.target.value);
              setOffset(0);
            }}
          />
          <Select
            placeholder="All departments"
            options={(departments.data ?? []).map((d) => ({ value: d.id, label: d.display_name }))}
            value={departmentId}
            onChange={(e) => {
              setDepartmentId(e.target.value);
              setOffset(0);
            }}
          />
          <Select
            placeholder="All languages"
            options={LANGUAGE_OPTIONS}
            value={language}
            onChange={(e) => {
              setLanguage(e.target.value);
              setOffset(0);
            }}
          />
          <Input
            type="date"
            value={dateFrom}
            onChange={(e) => {
              setDateFrom(e.target.value);
              setOffset(0);
            }}
          />
          <Input
            type="date"
            value={dateTo}
            onChange={(e) => {
              setDateTo(e.target.value);
              setOffset(0);
            }}
          />
        </div>
      </Card>

      <Card padded={false}>
        <DataTable<Call>
          columns={columns}
          rows={calls.data?.items ?? []}
          keyExtractor={(c) => c.id}
          isLoading={calls.isLoading}
          isError={calls.isError}
          onRetry={() => calls.refetch()}
          onRowClick={(c) => navigate(`/calls/${c.id}`)}
          emptyTitle="No calls yet"
          emptyDescription="Calls will appear here once Exotel starts routing to this line."
          sortKey={sort}
          sortOrder={order}
          onSortChange={toggleSort}
        />
        {calls.data && (
          <Pagination total={calls.data.total} limit={LIMIT} offset={offset} onOffsetChange={setOffset} />
        )}
      </Card>
    </div>
  );
}
