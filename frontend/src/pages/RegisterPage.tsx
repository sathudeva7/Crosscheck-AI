/**
 * Create an account, on Clerk's headless API.
 *
 * Two panels behind one route. Clerk emails a six-digit code before an account
 * exists, so "create" and "verify" are separate submissions — but they are one
 * step to the user, and a second URL would let them land on a code form with no
 * pending sign-up behind it.
 *
 * Deliberately short. The prototype puts the real questions — who the records
 * belong to, the patient's name, the consent statement — on the setup screen
 * that follows, so this stays at the two fields that create the account.
 */

import { useState } from "react";
import type React from "react";
import { Link } from "react-router-dom";
import { useSignUp } from "@clerk/clerk-react";
import {
  AuthHeader,
  AuthShell,
  Corners,
  FieldError,
  GoogleButton,
  OrDivider,
} from "../components/chrome";
import { useSubmit } from "../lib/useAuth";
import { useGoogle } from "../lib/useGoogle";

/** Long enough to be worth typing twice; short enough not to be theatre.
 *  Clerk enforces its own minimum too, and rejects breached passwords. */
const MIN_PASSWORD = 8;

/** Clerk's email codes are always six digits. */
const CODE_LENGTH = 6;

export default function RegisterPage() {
  const { isLoaded, signUp, setActive } = useSignUp();
  const { error, busy, submit, clearError } = useSubmit();
  const google = useGoogle("sign-up");

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [code, setCode] = useState("");
  const [awaitingCode, setAwaitingCode] = useState(false);

  const looksLikeEmail = /^\S+@\S+\.\S+$/.test(email.trim());
  const longEnough = password.length >= MIN_PASSWORD;
  const matches = confirm.length > 0 && confirm === password;
  const ready = isLoaded && looksLikeEmail && longEnough && matches;

  // Shown under the field as it is typed, rather than only on submit — a rule
  // you can see while typing is easier than one that fires afterwards.
  const passwordHint =
    password.length === 0
      ? `At least ${MIN_PASSWORD} characters.`
      : longEnough
        ? null
        : `${MIN_PASSWORD - password.length} more character${
            MIN_PASSWORD - password.length === 1 ? "" : "s"
          } needed.`;

  const confirmHint = confirm.length > 0 && !matches ? "Passwords do not match." : null;

  function onCreate(event: React.SyntheticEvent) {
    event.preventDefault();
    void submit(async () => {
      if (!isLoaded) return;
      await signUp.create({ emailAddress: email.trim(), password });
      await signUp.prepareEmailAddressVerification({ strategy: "email_code" });
      setAwaitingCode(true);
    });
  }

  function onVerify(event: React.SyntheticEvent) {
    event.preventDefault();
    void submit(async () => {
      if (!isLoaded) return;
      const attempt = await signUp.attemptEmailAddressVerification({ code: code.trim() });

      if (attempt.status !== "complete") {
        throw new Error("That code did not complete sign-up. Request a new one.");
      }

      // No redirect: activating the session moves the state machine to
      // `needs-setup`, and the router does the rest.
      await setActive({ session: attempt.createdSessionId });
    });
  }

  function resend() {
    void submit(async () => {
      if (!isLoaded) return;
      await signUp.prepareEmailAddressVerification({ strategy: "email_code" });
    });
  }

  if (awaitingCode) {
    return (
      <AuthShell width={460}>
        <AuthHeader step="Step 1 of 2 · Verify" />

        <form
          className="card blueprint"
          style={{ padding: "28px 26px", gap: 0 }}
          onSubmit={onVerify}
        >
          <Corners />

          <h2 style={{ margin: "0 0 6px", fontSize: 28 }}>Check your email</h2>
          <p
            style={{
              color: "color-mix(in srgb, var(--color-text) 60%, transparent)",
              marginBottom: 22,
            }}
          >
            We sent a {CODE_LENGTH}-digit code to <strong>{email.trim()}</strong>. Enter it to
            finish creating your account.
          </p>

          <div className="field" style={{ marginBottom: 4 }}>
            <label htmlFor="code">Verification code</label>
            <input
              id="code"
              className="input"
              inputMode="numeric"
              autoComplete="one-time-code"
              maxLength={CODE_LENGTH}
              placeholder="000000"
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
              // The code arrives while this screen is open, so focusing it
              // saves a click on every single sign-up.
              autoFocus
              style={{ letterSpacing: ".4em", fontSize: 18, textAlign: "center" }}
            />
          </div>

          <FieldError>{error}</FieldError>

          <button
            className="btn btn-primary btn-block"
            style={{ marginTop: 20, height: 46, fontSize: 16 }}
            disabled={code.length !== CODE_LENGTH || busy}
            type="submit"
          >
            {busy ? "Verifying…" : "Verify and continue →"}
          </button>

          <p
            style={{
              textAlign: "center",
              fontSize: 13,
              color: "color-mix(in srgb, var(--color-text) 55%, transparent)",
              margin: "14px 0 0",
            }}
          >
            Didn't get it?{" "}
            <button type="button" className="btn-link" onClick={resend} disabled={busy}>
              Send another
            </button>{" "}
            ·{" "}
            <button
              type="button"
              className="btn-link"
              onClick={() => {
                setAwaitingCode(false);
                setCode("");
                clearError();
              }}
            >
              Use a different email
            </button>
          </p>
        </form>
      </AuthShell>
    );
  }

  return (
    <AuthShell width={460}>
      <AuthHeader step="Step 1 of 2 · Account" />

      <form className="card blueprint" style={{ padding: "28px 26px", gap: 0 }} onSubmit={onCreate}>
        <Corners />

        <h2 style={{ margin: "0 0 6px", fontSize: 28 }}>Create your account</h2>
        <p
          style={{
            color: "color-mix(in srgb, var(--color-text) 60%, transparent)",
            marginBottom: 22,
          }}
        >
          One account holds every document, the merged timeline, and the cross-checks run
          across them.
        </p>

        <div className="field" style={{ marginBottom: 14 }}>
          <label htmlFor="email">Email address</label>
          <input
            id="email"
            className="input"
            type="email"
            autoComplete="email"
            placeholder="you@example.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </div>

        <div className="field" style={{ marginBottom: 14 }}>
          <label htmlFor="password">Password</label>
          <input
            id="password"
            className="input"
            type="password"
            autoComplete="new-password"
            placeholder="••••••••"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          {passwordHint && (
            <div
              style={{
                fontSize: 11.5,
                marginTop: 5,
                color: "color-mix(in srgb, var(--color-text) 50%, transparent)",
              }}
            >
              {passwordHint}
            </div>
          )}
        </div>

        <div className="field" style={{ marginBottom: 4 }}>
          <label htmlFor="confirm">Confirm password</label>
          <input
            id="confirm"
            className="input"
            type="password"
            autoComplete="new-password"
            placeholder="••••••••"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
          />
          {confirmHint && (
            <div style={{ fontSize: 11.5, marginTop: 5, color: "var(--color-danger-text)" }}>
              {confirmHint}
            </div>
          )}
        </div>

        <FieldError>{error || google.error}</FieldError>

        {/* Clerk renders its bot check here when the instance has one enabled. */}
        <div id="clerk-captcha" />

        <button
          className="btn btn-primary btn-block"
          style={{ marginTop: 20, height: 46, fontSize: 16 }}
          disabled={!ready || busy}
          type="submit"
        >
          {busy ? "Creating…" : "Continue to set up →"}
        </button>

        <OrDivider />

        {/* Google verifies the address itself, so this route skips the code
            panel above entirely and lands straight on setup. */}
        <GoogleButton
          label={google.busy ? "Redirecting…" : "Continue with Google"}
          onClick={google.start}
          disabled={!google.isLoaded || google.busy || busy}
        />

        <p
          style={{
            textAlign: "center",
            fontSize: 13,
            color: "color-mix(in srgb, var(--color-text) 55%, transparent)",
            margin: "14px 0 0",
          }}
        >
          Already have an account? <Link to="/login">Sign in</Link>
        </p>
      </form>
    </AuthShell>
  );
}
