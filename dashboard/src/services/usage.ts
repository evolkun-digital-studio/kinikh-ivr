import { apiRequest } from "@/services/api";
import type { CostPoint, UsageSummary } from "@/types/api";

export function getUsageSummary(): Promise<UsageSummary> {
  return apiRequest<UsageSummary>("/api/v1/usage/summary");
}

export function getUsageTimeseries(days = 30): Promise<CostPoint[]> {
  return apiRequest<CostPoint[]>("/api/v1/usage/timeseries", { query: { days } });
}
