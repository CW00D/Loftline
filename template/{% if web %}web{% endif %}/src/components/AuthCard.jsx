import { useState } from "react";
import { Link } from "react-router-dom";

// The frame every auth page sits in: a back link, the product name, a card.
export function AuthCard({ title, subtitle, back = "/", children }) {
  return (
    <main className="auth-page">
      <Link to={back} className="auth-back" aria-label="Back">
        ‹
      </Link>
      <div className="auth-page__inner">
        <Link to="/" className="auth-card__logo">
          {document.title}
        </Link>
        <div className="auth-card">
          <h1 className="auth-card__title">{title}</h1>
          {subtitle ? <p className="auth-card__subtitle">{subtitle}</p> : null}
          {children}
        </div>
      </div>
    </main>
  );
}

export function Notice({ kind = "error", children }) {
  if (!children) return null;
  return (
    <p className={`auth-card__${kind}`} role={kind === "error" ? "alert" : "status"}>
      {children}
    </p>
  );
}

export function Field({ id, label, problem, children }) {
  return (
    <div className={`auth-form__field${problem ? " is-invalid" : ""}`}>
      <label htmlFor={id}>{label}</label>
      {children}
      {problem ? <span className="auth-form__problem">{problem}</span> : null}
    </div>
  );
}

// A password box with a show/hide toggle, shared by every page that has one.
export function PasswordInput({ id, value, onChange, autoComplete, placeholder = "••••••••" }) {
  const [shown, setShown] = useState(false);
  return (
    <div className="auth-form__password-wrap">
      <input
        id={id}
        type={shown ? "text" : "password"}
        autoComplete={autoComplete}
        placeholder={placeholder}
        required
        value={value}
        onChange={onChange}
      />
      <button
        type="button"
        className="auth-form__eye"
        aria-label={shown ? "Hide password" : "Show password"}
        onClick={() => setShown((v) => !v)}
      >
        {shown ? (
          <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94" />
            <path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19" />
            <line x1="1" y1="1" x2="23" y2="23" />
          </svg>
        ) : (
          <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
            <circle cx="12" cy="12" r="3" />
          </svg>
        )}
      </button>
    </div>
  );
}
