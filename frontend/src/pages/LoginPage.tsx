/**
 * Sign in, on Clerk's headless API.
 *
 * Clerk ships a prebuilt <SignIn/> component, and it is not used here: it
 * carries its own visual language, and this app's is the prototype's. So the
 * screen stays hand-built from the same parts as the setup page — the
 * mark-and-wordmark header, one blueprint card with registration marks,
 * `.field`/`.input` rows, a 46px primary button — and `useSignIn` handles only
 * what happens when it is pressed.
 *
 * No redirect on success. Setting the session updates the provider, the state
 * machine moves to `ready` or `needs-setup`, and the router sends the user
 * where they belong. One rule for landing, not two.
 */

import { useState } from "react";
import type React from "react";
import { Link } from "react-router-dom";
import { useSignIn } from "@clerk/clerk-react";
import {
  AuthHeader,
  AuthShell,
  Corners,
  FieldError,
  GoogleButton,
  OrDivider,
} from "../components/chrome";
import { describeIncompleteSignIn } from "../lib/errors";
import { useSubmit } from "../lib/useAuth";
import { useGoogle } from "../lib/useGoogle";

/** Clerk's emailed codes are six digits. */
const CODE_LENGTH = 6;

export default function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const { isLoaded, signIn, setActive } = useSignIn();
  const { error, busy, submit, clearError } = useSubmit();
  const google = useGoogle("sign-in");

  /**
   * The second step, when Clerk asks for one.
   *
   * The password was accepted and the sign-in is half-finished; Clerk holds it
   * open on the `signIn` object until a code is supplied. Until this existed
   * the app read that state as an error and stopped, which is what made a
   * correct password look like a broken account.
   */
  const [secondFactor, setSecondFactor] = useState(false);
  const [code, setCode] = useState("");

  const ready = isLoaded && email.trim().length > 0 && password.length > 0;

  // SyntheticEvent rather than FormEvent: React 19's types deprecate the
  // latter, on the grounds that no such DOM event exists.
  function onSubmit(event: React.SyntheticEvent) {
    event.preventDefault();
    void submit(async () => {
      if (!isLoaded) return;

      const attempt = await signIn.create({ identifier: email.trim(), password });

      if (attempt.status === "needs_second_factor") {
        // Ask Clerk to send the code before showing a box to type it into —
        // a code field on screen with no email on its way is a dead end.
        await signIn.prepareSecondFactor({ strategy: "email_code" });
        setSecondFactor(true);
        return;
      }

      if (attempt.status !== "complete") {
        // Clerk answers with a status rather than an error when the attempt is
        // valid but unfinished, and there are several such statuses — an
        // account with no password, a browser that could not hold a session.
        // describeIncompleteSignIn tells them apart and logs the attempt,
        // rather than naming one cause for all of them.
        throw new Error(describeIncompleteSignIn(attempt));
      }

      await setActive({ session: attempt.createdSessionId });
    });
  }

  function onVerify(event: React.SyntheticEvent) {
    event.preventDefault();
    void submit(async () => {
      if (!isLoaded) return;

      const attempt = await signIn.attemptSecondFactor({
        strategy: "email_code",
        code: code.trim(),
      });

      if (attempt.status !== "complete") {
        throw new Error(describeIncompleteSignIn(attempt));
      }

      await setActive({ session: attempt.createdSessionId });
    });
  }

  function resend() {
    void submit(async () => {
      if (!isLoaded) return;
      await signIn.prepareSecondFactor({ strategy: "email_code" });
    });
  }

  if (secondFactor) {
    return (
      <AuthShell width={460}>
        <AuthHeader step="Step 2 of 2 · Verify" />

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
            Your password was accepted. We sent a {CODE_LENGTH}-digit code to{" "}
            <strong>{email.trim()}</strong> to finish signing in.
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
            {busy ? "Verifying…" : "Verify and sign in →"}
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
                setSecondFactor(false);
                setCode("");
                setPassword("");
                clearError();
              }}
            >
              Start over
            </button>
          </p>
        </form>
      </AuthShell>
    );
  }

  return (
    <AuthShell width={460}>
      <AuthHeader />

      <form className="card blueprint" style={{ padding: "28px 26px", gap: 0 }} onSubmit={onSubmit}>
        <Corners />

        <h2 style={{ margin: "0 0 6px", fontSize: 28 }}>Sign in</h2>
        <p
          style={{
            color: "color-mix(in srgb, var(--color-text) 60%, transparent)",
            marginBottom: 22,
          }}
        >
          Your records, your timeline, your cross-checks — all in one place.
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

        <div className="field" style={{ marginBottom: 4 }}>
          <label htmlFor="password">Password</label>
          <input
            id="password"
            className="input"
            type="password"
            autoComplete="current-password"
            placeholder="••••••••"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </div>

        <FieldError>{error || google.error}</FieldError>

        {/* Clerk's bot protection renders into this element when the instance
            has it switched on. Without it, sign-in fails with an unhelpful
            error the moment that setting is enabled. */}
        <div id="clerk-captcha" />

        <button
          className="btn btn-primary btn-block"
          style={{ marginTop: 20, height: 46, fontSize: 16 }}
          disabled={!ready || busy}
          type="submit"
        >
          {busy ? "Signing in…" : "Sign in"}
        </button>

        <OrDivider />

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
          No account yet? <Link to="/register">Create one</Link>
        </p>
      </form>
    </AuthShell>
  );
}
