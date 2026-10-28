import { Button } from "@/components/common/Button";

export function ErrorState({ message, onRetry }: { message?: string; onRetry?: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-md border border-danger/20 bg-danger-soft px-6 py-14 text-center">
      <p className="text-sm font-medium text-danger">Something went wrong.</p>
      {message && <p className="max-w-sm text-sm text-ink-muted">{message}</p>}
      {onRetry && (
        <Button variant="secondary" size="sm" className="mt-2" onClick={onRetry}>
          Retry
        </Button>
      )}
    </div>
  );
}
