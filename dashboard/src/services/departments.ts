import { apiRequest } from "@/services/api";
import type { Department } from "@/types/api";

export interface DepartmentInput {
  name: string;
  display_name: string;
  email: string;
  greeting?: string | null;
  prompt?: string | null;
  knowledge_base?: string | null;
  keywords?: string[] | null;
}

export function listDepartments(): Promise<Department[]> {
  return apiRequest<Department[]>("/api/v1/departments");
}

export function createDepartment(body: DepartmentInput): Promise<Department> {
  return apiRequest<Department>("/api/v1/departments", { method: "POST", body });
}

export function updateDepartment(id: string, body: Partial<DepartmentInput>): Promise<Department> {
  return apiRequest<Department>(`/api/v1/departments/${id}`, { method: "PATCH", body });
}

export function activateDepartment(id: string): Promise<Department> {
  return apiRequest<Department>(`/api/v1/departments/${id}/activate`, { method: "POST" });
}

export function deactivateDepartment(id: string): Promise<void> {
  return apiRequest<void>(`/api/v1/departments/${id}`, { method: "DELETE" });
}
