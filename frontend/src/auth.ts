// Auth state manager — singleton. Handles JWT token persistence, login, logout,
// and auth-expired events from apiClient.

import { apiFetch, getToken } from "./apiClient";
import { showToast } from "./toast";

const TOKEN_KEY = "tradewars-auth-token";
const REFRESH_KEY = "tradewars-refresh-token";
const USER_KEY = "tradewars-auth-user";

export interface AuthUser {
  username: string;
  display_name: string;
  is_admin: boolean;
}

let _user: AuthUser | null = null;
let _onAuthChange: ((user: AuthUser | null) => void) | null = null;

export function setAuthChangeHandler(
  handler: (user: AuthUser | null) => void,
): void {
  _onAuthChange = handler;
}

export function getUser(): AuthUser | null {
  return _user;
}

export async function initAuth(): Promise<AuthUser | null> {
  const token = getToken();
  if (!token) return null;

  try {
    const res = await apiFetch("/api/auth/me");
    if (!res.ok) {
      clearAuth();
      return null;
    }
    const data = await res.json();
    if (data.auth_disabled) {
      _user = { username: "dev", display_name: "Dev Mode", is_admin: true };
    } else {
      _user = {
        username: data.username,
        display_name: data.display_name,
        is_admin: !!data.is_admin,
      };
    }
    return _user;
  } catch {
    clearAuth();
    return null;
  }
}

export async function login(
  username: string,
  password: string,
): Promise<{ ok: boolean; error?: string }> {
  try {
    const res = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    const data = await res.json();
    if (!res.ok) {
      return { ok: false, error: data.detail || "Login failed" };
    }
    if (data.auth_disabled) {
      _user = { username: "dev", display_name: "Dev Mode", is_admin: true };
      _onAuthChange?.(_user);
      return { ok: true };
    }
    localStorage.setItem(TOKEN_KEY, data.token);
    if (data.refresh_token) {
      localStorage.setItem(REFRESH_KEY, data.refresh_token);
    }
    localStorage.setItem(
      USER_KEY,
      JSON.stringify({
        username: data.username,
        display_name: data.display_name,
        is_admin: !!data.is_admin,
      }),
    );
    _user = {
      username: data.username,
      display_name: data.display_name,
      is_admin: !!data.is_admin,
    };
    _onAuthChange?.(_user);
    return { ok: true };
  } catch {
    return { ok: false, error: "Network error — is the backend running?" };
  }
}

export function logout(): void {
  clearAuth();
  _onAuthChange?.(null);
}

function clearAuth(): void {
  _user = null;
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(REFRESH_KEY);
  localStorage.removeItem(USER_KEY);
}

window.addEventListener("auth-expired", () => {
  _user = null;
  showToast("Session expired — please log in again", "error");
  _onAuthChange?.(null);
});
