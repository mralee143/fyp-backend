import { create } from "zustand";
import { persist } from "zustand/middleware";

interface AuthState {
  token: string | null;
  email: string | null;
  setAuth: (token: string, email?: string | null) => void;
  clearAuth: () => void;
  isAuthenticated: () => boolean;
}

/**
 * Global auth store. The JWT is persisted to localStorage so the session
 * survives page reloads; the axios interceptor reads the token from here.
 */
export const useAuthStore = create<AuthState>()(
  persist(
    (set, get) => ({
      token: null,
      email: null,
      setAuth: (token, email = null) => set({ token, email }),
      clearAuth: () => set({ token: null, email: null }),
      isAuthenticated: () => Boolean(get().token),
    }),
    { name: "auth-storage" }
  )
);

/** Non-reactive token read for use outside React (e.g. axios interceptors). */
export const getToken = () => useAuthStore.getState().token;
