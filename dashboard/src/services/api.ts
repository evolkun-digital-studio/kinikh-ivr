import { authTokens, useAuthStore } from "@/stores/authStore";

const BASE_URL = import.meta.env.VITE_API_BASE_URL || window.location.origin;

export class ApiError extends Error {
  status: number;
  code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

export type QueryValue = string | number | boolean | undefined | null;

interface RequestOptions {
  method?: string;
  body?: unknown;
  query?: Record<string, QueryValue>;
  signal?: AbortSignal;
}

function buildUrl(path: string, query?: Record<string, QueryValue>): string {
  const url = new URL(`${BASE_URL}${path}`);
  if (query) {
    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined && value !== null && value !== "") {
        url.searchParams.set(key, String(value));
      }
    }
  }
  return url.toString();
}

async function rawRequest<T>(path: string, options: RequestOptions, token: string | null): Promise<T> {
  const response = await fetch(buildUrl(path, options.query), {
    method: options.method ?? "GET",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
    signal: options.signal,
  });

  if (response.status === 204) {
    return undefined as T;
  }

  const isJson = response.headers.get("content-type")?.includes("application/json");
  const data = isJson ? await response.json().catch(() => null) : null;

  if (!response.ok) {
    const code = data?.error?.code ?? data?.detail?.[0]?.type ?? "REQUEST_FAILED";
    const message = data?.error?.message ?? data?.detail ?? response.statusText ?? "Request failed";
    throw new ApiError(
      response.status,
      code,
      typeof message === "string" ? message : JSON.stringify(message),
    );
  }

  return data as T;
}

let refreshInFlight: Promise<string | null> | null = null;

async function refreshAccessToken(): Promise<string | null> {
  const refreshToken = authTokens.getRefresh();
  if (!refreshToken) return null;

  if (!refreshInFlight) {
    refreshInFlight = rawRequest<{ access_token: string; refresh_token: string }>(
      "/api/v1/auth/refresh",
      { method: "POST", body: { refresh_token: refreshToken } },
      null,
    )
      .then((tokens) => {
        useAuthStore.getState().setTokens(tokens.access_token, tokens.refresh_token);
        return tokens.access_token;
      })
      .catch(() => {
        useAuthStore.getState().clear();
        return null;
      })
      .finally(() => {
        refreshInFlight = null;
      });
  }
  return refreshInFlight;
}

/** Authenticated request helper: attaches the bearer token, retries once
 * after a silent refresh on 401, then clears the session and lets the
 * caller's UI redirect to /login. */
export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const token = authTokens.getAccess();
  try {
    return await rawRequest<T>(path, options, token);
  } catch (err) {
    if (err instanceof ApiError && err.status === 401 && token) {
      const newToken = await refreshAccessToken();
      if (newToken) {
        return rawRequest<T>(path, options, newToken);
      }
    }
    throw err;
  }
}

export async function publicRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  return rawRequest<T>(path, options, null);
}
