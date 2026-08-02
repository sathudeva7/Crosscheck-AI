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
import { useSubmit } from "../lib/useAuth";
import { useGoogle } from "../lib/useGoogle";

export default function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const { isLoaded, signIn, setActive } = useSignIn();
  const { error, busy, submit } = useSubmit();
  const google = useGoogle("sign-in");

  const ready = isLoaded && email.trim().length > 0 && password.length > 0;

  // SyntheticEvent rather than FormEvent: React 19's types deprecate the
  // latter, on the grounds that no such DOM event exists.
  function onSubmit(event: React.SyntheticEvent) {
    event.preventDefault();
    void submit(async () => {
      if (!isLoaded) return;

      const attempt = await signIn.create({ identifier: email.trim(), password });

      if (attempt.status !== "complete") {
        // Reachable when the Clerk instance requires a second factor. Nothing
        // in this app enables one, so it is a configuration mismatch rather
        // than something the user can resolve here.
        throw new Error("This account needs an extra verification step that is not set up yet.");
      }

      await setActive({ session: attempt.createdSessionId });
    });
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
