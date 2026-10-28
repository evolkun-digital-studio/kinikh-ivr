import { useMutation } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { login as loginRequest } from "@/services/auth";
import { useAuthStore } from "@/stores/authStore";

export function useAuth() {
  const navigate = useNavigate();
  const { isAuthenticated, setTokens, clear } = useAuthStore();

  const loginMutation = useMutation({
    mutationFn: loginRequest,
    onSuccess: (tokens) => {
      setTokens(tokens.access_token, tokens.refresh_token);
      navigate("/", { replace: true });
    },
  });

  const logout = () => {
    clear();
    navigate("/login", { replace: true });
  };

  return {
    isAuthenticated,
    login: loginMutation.mutateAsync,
    isLoggingIn: loginMutation.isPending,
    loginError: loginMutation.error,
    logout,
  };
}
