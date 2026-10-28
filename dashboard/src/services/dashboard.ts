import { apiRequest } from "@/services/api";
import type { DashboardSummary } from "@/types/api";

export function getDashboardSummary(): Promise<DashboardSummary> {
  return apiRequest<DashboardSummary>("/api/v1/dashboard/summary");
}
