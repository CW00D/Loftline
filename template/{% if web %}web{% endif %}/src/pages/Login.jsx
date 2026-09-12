import { useState } from "react";
import { Link, Navigate, useLocation, useNavigate, useSearchParams } from "react-router-dom";

import { AuthCard, Field, Notice, PasswordInput } from "@/components/AuthCard.jsx";
import { useAuth } from "@/contexts/useAuth.js";

function Login() {
  const { isAuthenticated, signIn } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [searchParams] = useSearchParams();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState(null);

  const resetSuccess = searchParams.get("reset") === "1";

  if (isAuthenticated) {
    return <Navigate to="/app" replace />;
  }

  async function handleSubmit(e) {
    e.preventDefault();
    setError(null);
    setIsSubmitting(true);
    try {
      await signIn("/login", { email: email.trim().toLowerCase(), password });
      navigate(location.state?.from?.pathname ?? "/app", { replace: true });
    } catch (err) {
      setError(err.message);
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <AuthCard title="Log in">
      <Notice kind="success">
        {resetSuccess ? "Password reset. Log in with your new password." : null}
      </Notice>
      <Notice>{error}</Notice>

      <form className="auth-form" onSubmit={handleSubmit}>
        <Field id="email" label="Email">
          <input
            id="email"
            type="email"
            autoComplete="email"
            placeholder="you@example.com"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </Field>

        <Field
          id="password"
          label={
            <>
              Password
              <Link to="/forgot-password" className="auth-form__aside">Forgot password?</Link>
            </>
          }
        >
          <PasswordInput
            id="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </Field>

        <button type="submit" className="auth-form__submit" disabled={isSubmitting}>
          {isSubmitting ? "Logging in…" : "Log in"}
        </button>
      </form>

      <p className="auth-card__footer">
        No account? <Link to="/signup">Sign up</Link>
      </p>
    </AuthCard>
  );
}

export default Login;
