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

/** Register a new account against POST /auth/signup (JSON body). Emails an OTP. */
export async function signup(email: string, password: string) {
  const { data } = await api.post("/auth/signup", { email, password });
  return data;
}

/** Verify the signup email OTP against POST /auth/verify-otp; stores the returned JWT (auto-login). */
export async function verifyOtp(email: string, code: string) {
  const { data } = await api.post<{ access_token: string; token_type: string }>(
    "/auth/verify-otp",
    { email, code }
  );
  useAuthStore.getState().setAuth(data.access_token, email);
  return data;
}

/** Request a fresh OTP against POST /auth/resend-otp. */
export async function resendOtp(email: string) {
  const { data } = await api.post("/auth/resend-otp", { email });
  return data;
}

/** Request a password-reset code (POST /auth/forgot-password). */
export async function forgotPassword(email: string) {
  const { data } = await api.post("/auth/forgot-password", { email });
  return data;
}

/** Set a new password with the emailed code (POST /auth/reset-password). */
export async function resetPassword(
  email: string,
  code: string,
  newPassword: string
) {
  const { data } = await api.post("/auth/reset-password", {
    email,
    code,
    new_password: newPassword,
  });
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
