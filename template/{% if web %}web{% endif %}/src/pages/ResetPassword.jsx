import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { AuthCard, Field, Notice, PasswordInput } from "@/components/AuthCard.jsx";
import { api } from "@/lib/api.js";

function ResetPassword() {
  const [searchParams] = useSearchParams();
  const emailParam = searchParams.get("email") ?? "";
  const navigate = useNavigate();

  const [email, setEmail] = useState(emailParam);
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [problems, setProblems] = useState({});
  const [error, setError] = useState(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  async function handleSubmit(e) {
    e.preventDefault();
    setError(null);
    const found = {};
    if (code.length !== 6) found.code = "The code is six digits";
    if (password.length < 8) found.password = "At least 8 characters";
    if (password !== confirmPassword) found.confirmPassword = "Passwords do not match";
    setProblems(found);
    if (Object.keys(found).length) return;

    setIsSubmitting(true);
    try {
      await api("/reset-password", {
        method: "POST",
        body: { email: email.trim().toLowerCase(), code, new_password: password },
      });
      navigate("/login?reset=1");
    } catch (err) {
      setError(err.message);
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <AuthCard
      title="Set a new password"
      subtitle="Enter the 6-digit code from your email and choose a new password."
      back="/forgot-password"
    >
      <Notice>{error}</Notice>

      <form className="auth-form" onSubmit={handleSubmit}>
        {!emailParam && (
          <Field id="email" label="Email">
            <input
              id="email"
              type="email"
              autoComplete="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </Field>
        )}

        <Field id="code" label="Reset code" problem={problems.code}>
          <input
            id="code"
            type="text"
            inputMode="numeric"
            autoComplete="one-time-code"
            placeholder="000000"
            maxLength={6}
            required
            value={code}
            onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
          />
        </Field>

        <Field id="password" label="New password" problem={problems.password}>
          <PasswordInput
            id="password"
            autoComplete="new-password"
            placeholder="min 8 characters"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </Field>

        <Field id="confirmPassword" label="Confirm new password" problem={problems.confirmPassword}>
          <input
            id="confirmPassword"
            type="password"
            autoComplete="new-password"
            required
            value={confirmPassword}
            onChange={(e) => setConfirmPassword(e.target.value)}
          />
        </Field>

        <button type="submit" className="auth-form__submit" disabled={isSubmitting}>
          {isSubmitting ? "Resetting…" : "Set new password"}
        </button>
      </form>

      <p className="auth-card__footer">
        <Link to="/login">Back to log in</Link>
      </p>
    </AuthCard>
  );
}

export default ResetPassword;
