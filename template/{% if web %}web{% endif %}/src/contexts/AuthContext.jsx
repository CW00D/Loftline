import { useCallback, useEffect, useState } from "react";

import { AuthContext } from "@/contexts/context.js";
import { api, onUnauthorized } from "@/lib/api.js";
import { clearToken, getToken, setToken } from "@/lib/auth.js";

// Read through useAuth() in ./useAuth.js. This file exports only the
// provider so that Vite's fast refresh can reload it in place.

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  // Loading only if there is a token to check: a stored token is verified
  // against /me before anything renders, so a page never flashes signed-out
  // and then signed-in.
  const [isLoading, setIsLoading] = useState(() => Boolean(getToken()));

  useEffect(() => {
    if (!getToken()) return;
    api("/me")
      .then(setUser)
      .catch(() => clearToken())
      .finally(() => setIsLoading(false));
  }, []);

  // A 401 on any later request ends the session everywhere at once.
  useEffect(() => {
    onUnauthorized(() => setUser(null));
    return () => onUnauthorized(null);
  }, []);

  // Both /login and /signup answer with a token; the caller decides which.
  const signIn = useCallback(async (path, body) => {
    const { token } = await api(path, { method: "POST", body });
    setToken(token);
    const me = await api("/me");
    setUser(me);
    return me;
  }, []);

  const signOut = useCallback(() => {
    clearToken();
    setUser(null);
  }, []);

  if (isLoading) {
    return <div className="auth-init-loader" aria-label="Loading" />;
  }

  return (
    <AuthContext.Provider
      value={{ user, setUser, isAuthenticated: user !== null, signIn, signOut }}
    >
      {children}
    </AuthContext.Provider>
  );
}
