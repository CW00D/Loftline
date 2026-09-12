// The session token, in localStorage. It is a signed JWT that lasts a week
// and is only ever sent as a bearer header to the API's origin.
const TOKEN_KEY = "auth_token";

export function getToken() {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token) {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken() {
  localStorage.removeItem(TOKEN_KEY);
}
