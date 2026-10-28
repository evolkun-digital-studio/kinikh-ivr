import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  activateDepartment,
  createDepartment,
  deactivateDepartment,
  DepartmentInput,
  listDepartments,
  updateDepartment,
} from "@/services/departments";
import { Card } from "@/components/cards/Card";
import { DataTable, Column } from "@/components/tables/DataTable";
import { StatusBadge } from "@/components/badges/StatusBadge";
import { Button } from "@/components/common/Button";
import { Input } from "@/components/common/Input";
import { Modal } from "@/components/modals/Modal";
import { Alert } from "@/components/common/Alert";
import type { Department } from "@/types/api";

const emptyForm: DepartmentInput = { name: "", display_name: "", email: "", greeting: "" };

export function Departments() {
  const queryClient = useQueryClient();
  const departments = useQuery({ queryKey: ["departments"], queryFn: listDepartments });

  const [isModalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<Department | null>(null);
  const [form, setForm] = useState<DepartmentInput>(emptyForm);
  const [formError, setFormError] = useState<string | null>(null);

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["departments"] });

  const createMutation = useMutation({
    mutationFn: createDepartment,
    onSuccess: () => {
      invalidate();
      setModalOpen(false);
    },
    onError: (err: Error) => setFormError(err.message),
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, body }: { id: string; body: DepartmentInput }) => updateDepartment(id, body),
    onSuccess: () => {
      invalidate();
      setModalOpen(false);
    },
    onError: (err: Error) => setFormError(err.message),
  });

  const toggleMutation = useMutation({
    mutationFn: async (dept: Department): Promise<void> => {
      if (dept.is_active) {
        await deactivateDepartment(dept.id);
      } else {
        await activateDepartment(dept.id);
      }
    },
    onSuccess: invalidate,
  });

  const openCreate = () => {
    setEditing(null);
    setForm(emptyForm);
    setFormError(null);
    setModalOpen(true);
  };

  const openEdit = (dept: Department) => {
    setEditing(dept);
    setForm({ name: dept.name, display_name: dept.display_name, email: dept.email, greeting: dept.greeting ?? "" });
    setFormError(null);
    setModalOpen(true);
  };

  const submit = () => {
    setFormError(null);
    if (editing) {
      updateMutation.mutate({ id: editing.id, body: form });
    } else {
      createMutation.mutate(form);
    }
  };

  const columns: Column<Department>[] = [
    { key: "name", header: "Department Name", render: (d) => <span className="font-medium">{d.display_name}</span> },
    { key: "email", header: "Email", render: (d) => d.email },
    { key: "status", header: "Status", render: (d) => <StatusBadge status={d.is_active ? "active" : "inactive"} /> },
    { key: "calls", header: "Calls", render: (d) => d.call_count },
    { key: "leads", header: "Leads", render: (d) => d.lead_count },
    {
      key: "actions",
      header: "Actions",
      render: (d) => (
        <div className="flex gap-2">
          <Button size="sm" variant="ghost" onClick={() => openEdit(d)}>
            Edit
          </Button>
          <Button
            size="sm"
            variant="ghost"
            isLoading={toggleMutation.isPending && toggleMutation.variables?.id === d.id}
            onClick={() => toggleMutation.mutate(d)}
          >
            {d.is_active ? "Deactivate" : "Activate"}
          </Button>
        </div>
      ),
    },
  ];

  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <Button variant="primary" onClick={openCreate}>
          New Department
        </Button>
      </div>

      <Card padded={false}>
        <DataTable<Department>
          columns={columns}
          rows={departments.data ?? []}
          keyExtractor={(d) => d.id}
          isLoading={departments.isLoading}
          isError={departments.isError}
          onRetry={() => departments.refetch()}
          emptyTitle="No departments yet"
          emptyDescription="Create a department to start routing calls and leads."
        />
      </Card>

      <Modal
        isOpen={isModalOpen}
        onClose={() => setModalOpen(false)}
        title={editing ? "Edit Department" : "New Department"}
        footer={
          <>
            <Button variant="secondary" onClick={() => setModalOpen(false)}>
              Cancel
            </Button>
            <Button
              variant="primary"
              isLoading={createMutation.isPending || updateMutation.isPending}
              onClick={submit}
            >
              Save
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          {formError && <Alert tone="danger">{formError}</Alert>}
          <div>
            <label className="mb-1.5 block text-sm font-medium text-ink">Internal Name</label>
            <Input
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="sales"
              disabled={Boolean(editing)}
            />
          </div>
          <div>
            <label className="mb-1.5 block text-sm font-medium text-ink">Display Name</label>
            <Input
              value={form.display_name}
              onChange={(e) => setForm({ ...form, display_name: e.target.value })}
              placeholder="Sales"
            />
          </div>
          <div>
            <label className="mb-1.5 block text-sm font-medium text-ink">Email</label>
            <Input
              type="email"
              value={form.email}
              onChange={(e) => setForm({ ...form, email: e.target.value })}
              placeholder="sales@company.com"
            />
          </div>
          <div>
            <label className="mb-1.5 block text-sm font-medium text-ink">Greeting (optional)</label>
            <Input
              value={form.greeting ?? ""}
              onChange={(e) => setForm({ ...form, greeting: e.target.value })}
            />
          </div>
        </div>
      </Modal>
    </div>
  );
}
