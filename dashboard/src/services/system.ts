import { apiRequest } from "@/services/api";
import type { IntegrationProvider, IntegrationTestResult, SystemStatus } from "@/types/api";

export function getSystemStatus(): Promise<SystemStatus> {
  return apiRequest<SystemStatus>("/api/v1/system/status");
}

export function testIntegration(provider: IntegrationProvider): Promise<IntegrationTestResult> {
  return apiRequest<IntegrationTestResult>(`/api/v1/system/integrations/${provider}/test`, {
    method: "POST",
  });
}
