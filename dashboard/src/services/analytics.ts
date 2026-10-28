import { apiRequest } from "@/services/api";
import type { AnalyticsOverview } from "@/types/api";

export function getAnalyticsOverview(days = 30): Promise<AnalyticsOverview> {
  return apiRequest<AnalyticsOverview>("/api/v1/analytics/overview", { query: { days } });
}
