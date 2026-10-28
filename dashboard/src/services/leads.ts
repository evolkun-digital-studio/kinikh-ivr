import { apiRequest, QueryValue } from "@/services/api";
import type { Lead, LeadDetail, Paginated } from "@/types/api";

export interface LeadFilters {
  department_id?: string;
  search?: string;
  date_from?: string;
  date_to?: string;
  sort?: string;
  order?: "asc" | "desc";
  limit?: number;
  offset?: number;
  [key: string]: QueryValue;
}

export function listLeads(filters: LeadFilters = {}): Promise<Paginated<Lead>> {
  return apiRequest<Paginated<Lead>>("/api/v1/leads", { query: filters });
}

export function getLead(id: string): Promise<LeadDetail> {
  return apiRequest<LeadDetail>(`/api/v1/leads/${id}`);
}
