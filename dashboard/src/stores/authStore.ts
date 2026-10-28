import { create } from "zustand";

const ACCESS_TOKEN_KEY = "kinikh_access_token";
const REFRESH_TOKEN_KEY = "kinikh_refresh_token";

interface AuthState {
  accessToken: string | null;
  refreshToken: string | null;
  isAuthenticated: boolean;
  setTokens: (accessToken: string, refreshToken: string) => void;
  clear: () => void;
}

export const useAuthStore = create<AuthState>((set) => ({
  accessToken: window.localStorage.getItem(ACCESS_TOKEN_KEY),
  refreshToken: window.localStorage.getItem(REFRESH_TOKEN_KEY),
  isAuthenticated: Boolean(window.localStorage.getItem(ACCESS_TOKEN_KEY)),
  setTokens: (accessToken, refreshToken) => {
    window.localStorage.setItem(ACCESS_TOKEN_KEY, accessToken);
    window.localStorage.setItem(REFRESH_TOKEN_KEY, refreshToken);
    set({ accessToken, refreshToken, isAuthenticated: true });
  },
  clear: () => {
    window.localStorage.removeItem(ACCESS_TOKEN_KEY);
    window.localStorage.removeItem(REFRESH_TOKEN_KEY);
    set({ accessToken: null, refreshToken: null, isAuthenticated: false });
  },
}));

// Non-hook accessors for use outside React components (the API client's
// interceptor logic runs in plain functions, not components).
export const authTokens = {
  getAccess: () => window.localStorage.getItem(ACCESS_TOKEN_KEY),
  getRefresh: () => window.localStorage.getItem(REFRESH_TOKEN_KEY),
};
