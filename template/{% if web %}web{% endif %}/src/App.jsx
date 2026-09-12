import { BrowserRouter, Route, Routes } from "react-router-dom";

import { AuthProvider } from "@/contexts/AuthContext.jsx";
import { ProtectedRoute } from "@/components/ProtectedRoute.jsx";
import Home from "@/pages/Home.jsx";
import Login from "@/pages/Login.jsx";
import Signup from "@/pages/Signup.jsx";
import ForgotPassword from "@/pages/ForgotPassword.jsx";
import ResetPassword from "@/pages/ResetPassword.jsx";
import Account from "@/pages/Account.jsx";
import NotFound from "@/pages/NotFound.jsx";

// Every route the base has. Public pages map one to one onto the API's auth
// endpoints; /app is where the product starts once signed in.
function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/login" element={<Login />} />
          <Route path="/signup" element={<Signup />} />
          <Route path="/forgot-password" element={<ForgotPassword />} />
          <Route path="/reset-password" element={<ResetPassword />} />
          <Route
            path="/app"
            element={
              <ProtectedRoute>
                <Account />
              </ProtectedRoute>
            }
          />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
}

export default App;
