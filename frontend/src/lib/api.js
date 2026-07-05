/**
 * Authenticated fetch helper. Automatically attaches the JWT from localStorage
 * to outgoing requests. Throws on non-2xx responses with the server's detail.
 */

const TOKEN_KEY = 'scheduler_ai_token';

// In production, VITE_API_BASE_URL points to the Render backend
// (e.g. https://scheduler-ai-api.onrender.com).
// In dev, it's empty so requests go to the Vite proxy (localhost:8000).
export const API_BASE = import.meta.env.VITE_API_BASE_URL || '';

export function getToken() {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token) {
  if (token) {
    localStorage.setItem(TOKEN_KEY, token);
  } else {
    localStorage.removeItem(TOKEN_KEY);
  }
}

export function clearToken() {
  localStorage.removeItem(TOKEN_KEY);
}

export class ApiError extends Error {
  constructor(message, status, data) {
    super(message);
    this.status = status;
    this.data = data;
  }
}

/**
 * apiFetch — fetch wrapper that attaches Bearer token and parses JSON.
 *
 * @param {string} path  — URL path (e.g. /api/auth/me)
 * @param {object} options — fetch options. Body objects are JSON-stringified.
 * @returns {Promise<any>} — parsed JSON response
 * @throws {ApiError} — on non-2xx responses
 */
export async function apiFetch(path, options = {}) {
  const token = getToken();
  const headers = {
    Accept: 'application/json',
    ...(options.headers || {}),
  };

  // Attach Bearer token if present
  if (token && !headers.Authorization) {
    headers.Authorization = `Bearer ${token}`;
  }

  // Auto-stringify object bodies. Also ensure Content-Type is set when the
  // caller already pre-stringified (very common in this codebase) — without it,
  // FastAPI rejects the body and returns a 422 that surfaces as "[object Object]".
  let body = options.body;
  if (body && typeof body === 'object' && !(body instanceof FormData)) {
    body = JSON.stringify(body);
    if (!headers['Content-Type']) headers['Content-Type'] = 'application/json';
  } else if (typeof body === 'string' && !(body instanceof FormData)) {
    if (!headers['Content-Type']) headers['Content-Type'] = 'application/json';
  }

  const res = await fetch(`${API_BASE}${path}`, { ...options, headers, body });

  // 401 → token expired/invalid, clear and let UI handle redirect
  if (res.status === 401) {
    clearToken();
    const data = await res.json().catch(() => ({}));
    throw new ApiError(data.detail || 'Unauthorized', 401, data);
  }

  // Parse JSON if possible
  const contentType = res.headers.get('content-type') || '';
  let data = null;
  if (contentType.includes('application/json')) {
    data = await res.json().catch(() => null);
  } else if (res.ok) {
    data = await res.text();
  }

  if (!res.ok) {
    const message = _extractErrorMessage(data, res.status);
    throw new ApiError(message, res.status, data);
  }

  return data;
}

/**
 * Pull a human-readable error message out of an API response body. FastAPI
 * uses `detail` which can be either a string (HTTPException) OR an array of
 * validation errors (Pydantic 422). We accept both shapes so callers don't
 * have to render "[object Object]" to the user.
 */
function _extractErrorMessage(data, status) {
  if (!data) return `Request failed with status ${status}`;
  const detail = data.detail;
  if (typeof detail === 'string' && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    // Pydantic 422 — each item has { loc: [...], msg, type }
    const parts = detail
      .map((d) => {
        if (!d || typeof d !== 'object') return null;
        const loc = Array.isArray(d.loc) ? d.loc.filter((p) => p !== 'body').join('.') : '';
        const msg = d.msg || d.message || '';
        return loc ? `${loc}: ${msg}` : msg;
      })
      .filter(Boolean);
    if (parts.length) return parts.join('; ');
  }
  if (detail && typeof detail === 'object') {
    return detail.msg || detail.message || JSON.stringify(detail);
  }
  if (typeof data.message === 'string') return data.message;
  return `Request failed with status ${status}`;
}
