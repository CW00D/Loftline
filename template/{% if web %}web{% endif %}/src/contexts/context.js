import { createContext } from "react";

// The context object on its own, so that AuthContext.jsx exports only a
// component and Vite's fast refresh can reload it in place.
export const AuthContext = createContext(null);
