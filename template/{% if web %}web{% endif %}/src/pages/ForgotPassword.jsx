import { useState } from "react";
import { Link } from "react-router-dom";

import { AuthCard, Field, Notice } from "@/components/AuthCard.jsx";
import { api } from "@/lib/api.js";

// The server answers /forgot-password identically whether or not the address
// is on an account, so this copy has to be true in both cases.
const SENT_NOTE = "If that email is on an account, a 6-digit code is on its way. It's good for an hour.";

function ForgotPassword() {
  const [email, setEmail] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState(null);
  const [sent, setSent] = useState(false);

  async function handleSubmit(e) {
    e.preventDefault();
    setError(null);
    setIsSubmitting(true);
    try {
      await api("/forgot-password", {
        method: "POST",
        body: { email: email.trim().toLowerCase() },
      });
      setSent(true);
    } catch (err) {
      setError(err.message);
    } finally {
      setIsSubmitting(false);
    }
  }

  if (sent) {
    return (
      <AuthCard title="Check your email" subtitle={SENT_NOTE} back="/login">
        <Link
          to={`/reset-password?email=${encodeURIComponent(email.trim().toLowerCase())}`}
          className="auth-form__submit"
        >
          Enter the code
        </Link>
        <p className="auth-card__footer">
          <Link to="/login">Back to log in</Link>
        </p>
      </AuthCard>
    );
  }

  return (
    <AuthCard
      title="Reset password"
      subtitle="Enter the email you signed up with and we'll send you a code."
      back="/login"
    >
      <Notice>{error}</Notice>

      <form className="auth-form" onSubmit={handleSubmit}>
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

        <button type="submit" className="auth-form__submit" disabled={isSubmitting}>
          {isSubmitting ? "Sending…" : "Send code"}
        </button>
      </form>

      <p className="auth-card__footer">
        <Link to="/login">Back to log in</Link>
      </p>
    </AuthCard>
  );
}

export default ForgotPassword;
