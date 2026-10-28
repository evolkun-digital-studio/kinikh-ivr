import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { listLeads } from "@/services/leads";
import { listDepartments } from "@/services/departments";
import { Card } from "@/components/cards/Card";
import { DataTable, Column } from "@/components/tables/DataTable";
import { Input, Select } from "@/components/common/Input";
import { Pagination } from "@/components/common/Pagination";
import { useDebounce } from "@/hooks/useDebounce";
import { formatDateTime, languageLabel } from "@/utils/format";
import type { Lead } from "@/types/api";

const LIMIT = 20;

export function Leads() {
  const navigate = useNavigate();
  const [search, setSearch] = useState("");
  const [departmentId, setDepartmentId] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [sort, setSort] = useState("created_at");
  const [order, setOrder] = useState<"asc" | "desc">("desc");
  const [offset, setOffset] = useState(0);

  const debouncedSearch = useDebounce(search);
  const departments = useQuery({ queryKey: ["departments"], queryFn: listDepartments });

  const filters = {
    search: debouncedSearch || undefined,
    department_id: departmentId || undefined,
    date_from: dateFrom || undefined,
    date_to: dateTo || undefined,
    sort,
    order,
    limit: LIMIT,
    offset,
  };

  const leads = useQuery({ queryKey: ["leads", filters], queryFn: () => listLeads(filters) });

  const toggleSort = (key: string) => {
    if (sort === key) setOrder(order === "asc" ? "desc" : "asc");
    else {
      setSort(key);
      setOrder("desc");
    }
    setOffset(0);
  };

  const columns: Column<Lead>[] = [
    { key: "name", header: "Name", sortKey: "name", render: (l) => l.name ?? "—" },
    { key: "phone", header: "Phone", render: (l) => l.phone },
    { key: "email", header: "Email", render: (l) => l.email ?? "—" },
    { key: "department", header: "Department", render: (l) => l.department?.display_name ?? "—" },
    {
      key: "requirement",
      header: "Requirements",
      render: (l) => <span className="line-clamp-1 max-w-[14rem] block">{l.requirement ?? "—"}</span>,
    },
    { key: "language", header: "Language", render: (l) => languageLabel(l.language) },
    { key: "created_at", header: "Created", sortKey: "created_at", render: (l) => formatDateTime(l.created_at) },
    {
      key: "action",
      header: "",
      render: (l) => (
        <button
          onClick={(e) => {
            e.stopPropagation();
            navigate(`/leads/${l.id}`);
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
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Input
            placeholder="Search name, phone, email…"
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setOffset(0);
            }}
            className="col-span-2"
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
          <div className="grid grid-cols-2 gap-2">
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
        </div>
      </Card>

      <Card padded={false}>
        <DataTable<Lead>
          columns={columns}
          rows={leads.data?.items ?? []}
          keyExtractor={(l) => l.id}
          isLoading={leads.isLoading}
          isError={leads.isError}
          onRetry={() => leads.refetch()}
          onRowClick={(l) => navigate(`/leads/${l.id}`)}
          emptyTitle="No leads yet"
          emptyDescription="Captured leads from inbound calls will show up here."
          sortKey={sort}
          sortOrder={order}
          onSortChange={toggleSort}
        />
        {leads.data && (
          <Pagination total={leads.data.total} limit={LIMIT} offset={offset} onOffsetChange={setOffset} />
        )}
      </Card>
    </div>
  );
}
