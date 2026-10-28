import { FormEvent, useState } from "react";
import { useAuth } from "@/hooks/useAuth";
import { Button } from "@/components/common/Button";
import { Input } from "@/components/common/Input";
import { Alert } from "@/components/common/Alert";
import { ApiError } from "@/services/api";

export function Login() {
  const { login, isLoggingIn, loginError } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    try {
      await login({ email, password });
    } catch {
      // surfaced via loginError below
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-canvas px-4">
      <div className="w-full max-w-sm">
        <div className="mb-8 flex flex-col items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-ink text-base font-bold text-white">
            K
          </div>
          <div className="text-center">
            <h1 className="text-lg font-semibold text-ink">Kinikh IVR</h1>
            <p className="text-sm text-ink-muted">Sign in to the admin dashboard</p>
          </div>
        </div>

        <form onSubmit={onSubmit} className="rounded-lg border border-border bg-white p-6 shadow-card">
          <div className="space-y-4">
            {loginError && (
              <Alert tone="danger">
                {loginError instanceof ApiError ? loginError.message : "Login failed"}
              </Alert>
            )}
            <div>
              <label className="mb-1.5 block text-sm font-medium text-ink">Email</label>
              <Input
                type="email"
                required
                autoComplete="username"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="admin@company.com"
              />
            </div>
            <div>
              <label className="mb-1.5 block text-sm font-medium text-ink">Password</label>
              <Input
                type="password"
                required
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••"
              />
            </div>
            <Button type="submit" variant="primary" className="w-full" isLoading={isLoggingIn}>
              Sign in
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
