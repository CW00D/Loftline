import { useState } from "react";
import { Link, Navigate, useNavigate } from "react-router-dom";

import { AuthCard, Field, Notice, PasswordInput } from "@/components/AuthCard.jsx";
import { useAuth } from "@/contexts/useAuth.js";

function Signup() {
  const { isAuthenticated, signIn } = useAuth();
  const navigate = useNavigate();

  const [name, setName] = useState("");
  const [handle, setHandle] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  // Keyed by the API's own field names, so a 422 or the 409 on a taken handle
  // can be pointed at the right box.
  const [problems, setProblems] = useState({});
  const [error, setError] = useState(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  if (isAuthenticated) {
    return <Navigate to="/app" replace />;
  }

  function edit(setter, key) {
    return (e) => {
      setter(e.target.value);
      setError(null);
      setProblems((p) => (p[key] ? { ...p, [key]: null } : p));
    };
  }

  async function handleSubmit(e) {
    e.preventDefault();
    setError(null);
    // Every rule the server applies, applied here first, so "min 8
    // characters" arrives as a sentence under the box rather than as a 422
    // the form cannot explain. A taken handle still comes back from the
    // server and is flagged the same way.
    const found = {};
    if (!name.trim()) found.name = "Enter your name";
    if (!handle.trim()) found.handle = "Pick a handle";
    else if (!/^[a-z0-9_]{2,30}$/.test(handle.trim().toLowerCase())) {
      found.handle = "Letters, numbers and underscores only, 2 to 30 characters";
    }
    if (password.length < 8) found.password = "At least 8 characters";
    if (password !== confirmPassword) found.confirmPassword = "Passwords do not match";
    setProblems(found);
    if (Object.keys(found).length) return;

    setIsSubmitting(true);
    try {
      await signIn("/signup", {
        email: email.trim().toLowerCase(),
        password,
        handle: handle.trim().toLowerCase(),
        name: name.trim(),
      });
      navigate("/app", { replace: true });
    } catch (err) {
      const field =
        err.fields?.[0] ??
        (err.status === 409 && /handle/i.test(err.message) ? "handle" : null) ??
        (err.status === 409 && /email/i.test(err.message) ? "email" : null);
      if (field) setProblems({ [field]: err.message });
      else setError(err.message);
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <AuthCard title="Create account">
      <Notice>{error}</Notice>

      <form className="auth-form" onSubmit={handleSubmit}>
        <Field id="name" label="Name" problem={problems.name}>
          <input id="name" type="text" autoComplete="name" required value={name} onChange={edit(setName, "name")} />
        </Field>

        <Field id="handle" label="Handle" problem={problems.handle}>
          <input
            id="handle"
            type="text"
            autoCapitalize="none"
            autoCorrect="off"
            placeholder="lowercase, no spaces"
            required
            value={handle}
            onChange={edit(setHandle, "handle")}
          />
        </Field>

        <Field id="email" label="Email" problem={problems.email}>
          <input id="email" type="email" autoComplete="email" required value={email} onChange={edit(setEmail, "email")} />
        </Field>

        <Field id="password" label="Password" problem={problems.password}>
          <PasswordInput
            id="password"
            autoComplete="new-password"
            placeholder="min 8 characters"
            value={password}
            onChange={edit(setPassword, "password")}
          />
        </Field>

        <Field id="confirmPassword" label="Confirm password" problem={problems.confirmPassword}>
          <input
            id="confirmPassword"
            type="password"
            autoComplete="new-password"
            required
            value={confirmPassword}
            onChange={edit(setConfirmPassword, "confirmPassword")}
          />
        </Field>

        <button type="submit" className="auth-form__submit" disabled={isSubmitting}>
          {isSubmitting ? "Creating account…" : "Sign up"}
        </button>
      </form>

      <p className="auth-card__footer">
        Have an account? <Link to="/login">Log in</Link>
      </p>
    </AuthCard>
  );
}

export default Signup;
