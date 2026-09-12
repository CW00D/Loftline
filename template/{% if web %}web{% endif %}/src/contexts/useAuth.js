import { useContext } from "react";

import { AuthContext } from "./context.js";

export function useAuth() {
  return useContext(AuthContext);
}
