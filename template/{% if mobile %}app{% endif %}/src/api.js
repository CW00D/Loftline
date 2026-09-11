import { clearToken, getToken } from './auth';

export const BASE_URL = process.env.EXPO_PUBLIC_API_URL;

// fetch has no timeout of its own. Without this, a captive-portal wifi that
// accepts the connection and never answers leaves a spinner running forever.
const TIMEOUT_MS = 15000;

// Called when an authenticated request comes back 401: the token expired
// part-way through a session. A module-level hook rather than a context,
// because api() is a plain function called from every screen.
let unauthorized = null;

export function onUnauthorized(fn) {
  unauthorized = fn;
}

// FastAPI hands back two shapes under `detail`. A route that raised
// HTTPException gives a sentence written for a person, which is always the
// best thing to show. A 422 from Pydantic gives a list of machine records,
// which is not. These are one short line each because they land in a Banner.
const FALLBACK = {
  404: "Can't reach the server. The app may need updating",
  408: 'That took too long. Try again',
  429: "That's a lot of tries. Give it a minute",
};

function describe(status, detail) {
  if (typeof detail === 'string' && detail) return detail;
  if (status === 422) return 'Details entered are incorrect';
  if (FALLBACK[status]) return FALLBACK[status];
  if (status >= 500) return 'The server is having a moment. Try again shortly';
  return 'Something went wrong. Try again';
}

// Which fields Pydantic objected to, so a form can point at them. `loc` is a
// path like ['body', 'email']; the field is the last hop of it.
function invalidFields(detail) {
  if (!Array.isArray(detail)) return [];
  return detail
    .map((d) => (Array.isArray(d?.loc) ? d.loc[d.loc.length - 1] : null))
    .filter((f) => typeof f === 'string');
}

export async function api(path, { method = 'GET', body } = {}) {
  if (!BASE_URL) {
    throw new Error('EXPO_PUBLIC_API_URL is not set. See app/.env.example');
  }
  const token = await getToken();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  let res;
  let data;
  try {
    res = await fetch(`${BASE_URL}${path}`, {
      method,
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: body ? JSON.stringify(body) : undefined,
      signal: controller.signal,
    });
    // Read the body inside the same timeout: headers that arrive with a body
    // that never does would otherwise hang here with the timer cleared.
    data = await res.json().catch((err) => {
      if (controller.signal.aborted) throw err;
      return null;
    });
  } catch (e) {
    // fetch rejects for one reason: the request never got an answer. Callers
    // must be able to tell that apart from the server refusing, because it is
    // the difference between offering a retry and logging somebody out.
    const error = new Error(
      controller.signal.aborted
        ? "The server didn't answer in time. Check your connection and try again."
        : "Couldn't reach the server. Check your connection and try again."
    );
    error.offline = true;
    throw error;
  } finally {
    clearTimeout(timer);
  }
  if (!res.ok) {
    const detail = data && data.detail;
    if (__DEV__ && typeof detail !== 'string') {
      console.warn(`api ${method} ${path} -> ${res.status}`, detail ?? '(no body)');
    }
    const error = new Error(describe(res.status, detail));
    error.status = res.status;
    error.fields = invalidFields(detail);
    // The token was good enough to send and the server rejected it anyway, so
    // it has expired or the account is gone. Drop it and say so once.
    // Gated on `token` so /login and /signup answering 401 to a wrong password
    // never trip it: there is no session to end there.
    if (res.status === 401 && token) {
      await clearToken();
      unauthorized?.();
    }
    throw error;
  }
  return data;
}
