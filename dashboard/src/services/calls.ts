import { apiRequest, QueryValue } from "@/services/api";
import type { Call, CallDetail, Paginated } from "@/types/api";

export interface CallFilters {
  status?: string;
  department_id?: string;
  language?: string;
  search?: string;
  date_from?: string;
  date_to?: string;
  sort?: string;
  order?: "asc" | "desc";
  limit?: number;
  offset?: number;
  [key: string]: QueryValue;
}

export function listCalls(filters: CallFilters = {}): Promise<Paginated<Call>> {
  return apiRequest<Paginated<Call>>("/api/v1/calls", { query: filters });
}

export function getCall(id: string): Promise<CallDetail> {
  return apiRequest<CallDetail>(`/api/v1/calls/${id}`);
}
