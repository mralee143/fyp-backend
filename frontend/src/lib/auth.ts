import { api } from "@/lib/api";
import { useAuthStore } from "@/store/auth";

/**
 * Log in against POST /auth/login.
 * The backend uses OAuth2PasswordRequestForm, so credentials must be sent
 * as application/x-www-form-urlencoded with `username` + `password`.
 */
export async function login(email: string, password: string) {
  const body = new URLSearchParams();
  body.append("username", email);
  body.append("password", password);

  const { data } = await api.post<{ access_token: string; token_type: string }>(
    "/auth/login",
    body,
    { headers: { "Content-Type": "application/x-www-form-urlencoded" } }
  );

  useAuthStore.getState().setAuth(data.access_token, email);
  return data;
}

/** Register a new account against POST /auth/signup (JSON body). */
export async function signup(email: string, password: string) {
  const { data } = await api.post("/auth/signup", { email, password });
  return data;
}

/** Clear the local session. */
export function logout() {
  useAuthStore.getState().clearAuth();
}

/** Turn an axios error into a human-readable message from FastAPI's `detail`. */
export function errorMessage(err: unknown): string {
  const e = err as {
    response?: { data?: { detail?: unknown }; status?: number };
    message?: string;
  };
  const detail = e?.response?.data?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && detail[0]?.msg) return String(detail[0].msg);
  if (e?.response?.status) return `Request failed (${e.response.status})`;
  return e?.message ?? "Something went wrong. Is the backend running?";
}
