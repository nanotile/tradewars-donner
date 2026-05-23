// Fetch wrapper: injects JWT Authorization header, auto-refreshes on 401.

const TOKEN_KEY = "tradewars-auth-token";
const REFRESH_KEY = "tradewars-refresh-token";
const USER_KEY = "tradewars-auth-user";

let _refreshing: Promise<boolean> | null = null;

async function tryRefresh(): Promise<boolean> {
  const rt = localStorage.getItem(REFRESH_KEY);
  if (!rt) return false;
  try {
    const res = await fetch("/api/auth/refresh", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: rt }),
    });
    if (!res.ok) return false;
    const data = await res.json();
    if (data.auth_disabled) return true;
    localStorage.setItem(TOKEN_KEY, data.token);
    localStorage.setItem(REFRESH_KEY, data.refresh_token);
    return true;
  } catch {
    return false;
  }
}

export async function apiFetch(
  input: string,
  init: RequestInit = {},
): Promise<Response> {
  const token = localStorage.getItem(TOKEN_KEY) || "";
  const headers: Record<string, string> = {
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...((init.headers as Record<string, string>) || {}),
  };
  let res = await fetch(input, { ...init, headers });

  if (res.status === 401 && !input.includes("/api/auth/")) {
    if (!_refreshing) _refreshing = tryRefresh();
    const ok = await _refreshing;
    _refreshing = null;
    if (ok) {
      const newToken = localStorage.getItem(TOKEN_KEY) || "";
      const retryHeaders: Record<string, string> = {
        ...(newToken ? { Authorization: `Bearer ${newToken}` } : {}),
        ...((init.headers as Record<string, string>) || {}),
      };
      res = await fetch(input, { ...init, headers: retryHeaders });
    }
    if (res.status === 401) {
      localStorage.removeItem(TOKEN_KEY);
      localStorage.removeItem(REFRESH_KEY);
      localStorage.removeItem(USER_KEY);
      window.dispatchEvent(new CustomEvent("auth-expired"));
    }
  }

  return res;
}

export function apiPost(
  input: string,
  body?: unknown,
): Promise<Response> {
  return apiFetch(input, {
    method: "POST",
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
}

export function getToken(): string {
  return localStorage.getItem(TOKEN_KEY) || "";
}
