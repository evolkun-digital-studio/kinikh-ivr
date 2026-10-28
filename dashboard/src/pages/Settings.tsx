import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getSystemStatus, testIntegration } from "@/services/settings";
import { Card } from "@/components/cards/Card";
import { StatusBadge } from "@/components/badges/StatusBadge";
import { Button } from "@/components/common/Button";
import { ErrorState } from "@/components/common/ErrorState";
import { Skeleton } from "@/components/common/Skeleton";
import { ApiError } from "@/services/api";
import { formatDateTime } from "@/utils/format";
import type { IntegrationProvider, IntegrationTestResult, ProviderStatus } from "@/types/api";

function ProviderTestCard({
  provider,
  providerKey,
}: {
  provider: ProviderStatus | undefined;
  providerKey: IntegrationProvider;
}) {
  const queryClient = useQueryClient();

  const testMutation = useMutation({
    mutationFn: () => testIntegration(providerKey),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["system-status"] });
    },
  });

  const isConnectedForMask = provider?.status === "connected";
  const lastResult: IntegrationTestResult | undefined = testMutation.data;
  const lastError = testMutation.error;

  return (
    <div className="flex flex-col gap-2 border-b border-border py-3 last:border-0 sm:flex-row sm:items-center sm:justify-between">
      <div className="min-w-0">
        <p className="text-sm font-medium capitalize text-ink">{provider?.provider ?? providerKey}</p>
        <p className="text-xs text-ink-faint">{provider?.detail}</p>
        {provider?.last_tested_at && (
          <p className="text-xs text-ink-faint">Last tested {formatDateTime(provider.last_tested_at)}</p>
        )}
        {lastResult && (
          <p className={`mt-1 text-xs ${lastResult.status === "connected" ? "text-success" : "text-danger"}`}>
            {lastResult.status === "connected" ? "✓" : "✕"} {lastResult.message}
          </p>
        )}
        {lastError && (
          <p className="mt-1 text-xs text-danger">
            ✕ {lastError instanceof ApiError ? lastError.message : "Connection test failed."}
          </p>
        )}
      </div>
      <div className="flex shrink-0 items-center gap-3">
        {isConnectedForMask && <span className="font-mono text-xs text-ink-faint">••••••••••••</span>}
        <StatusBadge status={provider?.status} />
        <Button
          size="sm"
          variant="secondary"
          isLoading={testMutation.isPending}
          disabled={testMutation.isPending}
          onClick={() => testMutation.mutate()}
        >
          {testMutation.isPending ? "Testing..." : "Test Connection"}
        </Button>
      </div>
    </div>
  );
}

export function Settings() {
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["system-status"],
    queryFn: getSystemStatus,
  });

  if (isError) return <ErrorState onRetry={() => refetch()} />;

  const find = (name: IntegrationProvider) => data?.providers.find((p) => p.provider === name);

  return (
    <div className="space-y-4">
      <Card title="AI">
        {isLoading ? <Skeleton className="h-16 w-full" /> : <ProviderTestCard provider={find("openai")} providerKey="openai" />}
      </Card>

      <Card title="Voice">
        {isLoading ? (
          <Skeleton className="h-16 w-full" />
        ) : (
          <ProviderTestCard provider={find("elevenlabs")} providerKey="elevenlabs" />
        )}
      </Card>

      <Card title="Telephony">
        {isLoading ? <Skeleton className="h-16 w-full" /> : <ProviderTestCard provider={find("exotel")} providerKey="exotel" />}
      </Card>

      <Card title="Email">
        {isLoading ? <Skeleton className="h-16 w-full" /> : <ProviderTestCard provider={find("email")} providerKey="email" />}
      </Card>

      <Card title="System">
        {isLoading ? (
          <Skeleton className="h-24 w-full" />
        ) : (
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <div>
              <p className="text-xs font-medium uppercase tracking-wide text-ink-faint">Environment</p>
              <p className="mt-0.5 text-sm capitalize text-ink">{data?.environment}</p>
            </div>
            <div>
              <p className="text-xs font-medium uppercase tracking-wide text-ink-faint">Backend</p>
              <div className="mt-0.5">
                <StatusBadge status="connected" />
              </div>
            </div>
            <div>
              <p className="text-xs font-medium uppercase tracking-wide text-ink-faint">Database</p>
              <div className="mt-0.5">
                <StatusBadge status={data?.database} />
              </div>
            </div>
            <div>
              <p className="text-xs font-medium uppercase tracking-wide text-ink-faint">Application Version</p>
              <p className="mt-0.5 text-sm text-ink">{data?.app_version}</p>
            </div>
          </div>
        )}
      </Card>
    </div>
  );
}
