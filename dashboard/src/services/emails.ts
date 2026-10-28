import { apiRequest, QueryValue } from "@/services/api";
import type { EmailLog, Paginated } from "@/types/api";

export interface EmailFilters {
  status?: string;
  department_id?: string;
  search?: string;
  date_from?: string;
  date_to?: string;
  limit?: number;
  offset?: number;
  [key: string]: QueryValue;
}

export function listEmails(filters: EmailFilters = {}): Promise<Paginated<EmailLog>> {
  return apiRequest<Paginated<EmailLog>>("/api/v1/emails", { query: filters });
}
