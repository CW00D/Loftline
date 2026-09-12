import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { Field, Notice, PasswordInput } from "@/components/AuthCard.jsx";
import { useAuth } from "@/contexts/useAuth.js";
import { api } from "@/lib/api.js";

// Where the site lands once signed in. Deliberately little here: the product
// starts on this page. It proves the round trip (a stored token, /me, a name
// on screen), exercises the two authenticated writes, and gives the session
// a way out.
function Account() {
  const { user, signOut } = useAuth();
  const navigate = useNavigate();

  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [notice, setNotice] = useState(null);
  const [error, setError] = useState(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  async function changePassword(e) {
    e.preventDefault();
    setNotice(null);
    setError(null);
    if (newPassword.length < 8) {
      setError("The new password needs at least 8 characters");
      return;
    }
    setIsSubmitting(true);
    try {
      await api("/me/password", {
        method: "POST",
        body: { current_password: currentPassword, new_password: newPassword },
      });
      setCurrentPassword("");
      setNewPassword("");
      setNotice("Password changed");
    } catch (err) {
      setError(err.message);
    } finally {
      setIsSubmitting(false);
    }
  }

  async function deleteAccount() {
    if (!window.confirm("Delete your account? This cannot be undone.")) return;
    try {
      await api("/me", { method: "DELETE" });
      signOut();
      navigate("/", { replace: true });
    } catch (err) {
      setError(err.message);
    }
  }

  function logOut() {
    signOut();
    navigate("/", { replace: true });
  }

  return (
    <main className="account">
      <header className="account__header">
        <Link to="/" className="account__logo">{document.title}</Link>
        <button type="button" className="button button--ghost" onClick={logOut}>
          Log out
        </button>
      </header>

      <section className="account__card">
        <h1 className="account__title">Signed in</h1>
        <p className="account__who">
          {user.name} <span className="account__handle">@{user.handle}</span>
        </p>
        <p className="account__email">{user.email}</p>
      </section>

      <section className="account__card">
        <h2 className="account__subtitle">Change password</h2>
        <Notice kind="success">{notice}</Notice>
        <Notice>{error}</Notice>
        <form className="auth-form" onSubmit={changePassword}>
          <Field id="currentPassword" label="Current password">
            <PasswordInput
              id="currentPassword"
              autoComplete="current-password"
              value={currentPassword}
              onChange={(e) => setCurrentPassword(e.target.value)}
            />
          </Field>
          <Field id="newPassword" label="New password">
            <PasswordInput
              id="newPassword"
              autoComplete="new-password"
              placeholder="min 8 characters"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
            />
          </Field>
          <button type="submit" className="auth-form__submit" disabled={isSubmitting}>
            {isSubmitting ? "Saving…" : "Change password"}
          </button>
        </form>
      </section>

      <section className="account__card account__card--danger">
        <h2 className="account__subtitle">Delete account</h2>
        <p className="account__note">Removes the account and everything on it.</p>
        <button type="button" className="button button--danger" onClick={deleteAccount}>
          Delete my account
        </button>
      </section>
    </main>
  );
}

export default Account;
