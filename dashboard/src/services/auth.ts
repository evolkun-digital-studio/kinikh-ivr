import { publicRequest } from "@/services/api";
import type { LoginRequest, TokenResponse } from "@/types/api";

export function login(body: LoginRequest): Promise<TokenResponse> {
  return publicRequest<TokenResponse>("/api/v1/auth/login", { method: "POST", body });
}
