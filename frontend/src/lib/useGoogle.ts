/**
 * Continue with Google, from either auth screen.
 *
 * Clerk models sign-in and sign-up as separate resources, but Google does not:
 * one button has to serve both, and which it turns out to be depends on whether
 * that Google account has been here before. Clerk resolves this itself —
 * `signIn.authenticateWithRedirect` transfers to a sign-up when no account
 * matches, and vice versa — so both screens can call the same hook and neither
 * has to guess.
 *
 * The redirect leaves the app entirely, so nothing after the call runs. That is
 * also why `busy` is never cleared: the page is on its way out, and re-enabling
 * the button would only invite a second click.
 */

import { useCallback, useState } from "react";
import { useSignIn, useSignUp } from "@clerk/clerk-react";
import { describeError } from "./errors";

/** Where Google sends the browser back to. Must be a route in this app. */
export const SSO_CALLBACK_PATH = "/sso-callback";

export function useGoogle(mode: "sign-in" | "sign-up") {
  const { isLoaded: signInLoaded, signIn } = useSignIn();
  const { isLoaded: signUpLoaded, signUp } = useSignUp();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const isLoaded = signInLoaded && signUpLoaded;

  const start = useCallback(() => {
    if (!isLoaded) return;
    setError(null);
    setBusy(true);

    const resource = mode === "sign-up" ? signUp : signIn;

    resource
      .authenticateWithRedirect({
        strategy: "oauth_google",
        redirectUrl: SSO_CALLBACK_PATH,
        // Back to the root, so the existing state machine decides where the
        // user lands: /setup for a new account, /app for a returning one. One
        // landing rule, not a second one hidden in the OAuth path.
        redirectUrlComplete: "/",
      })
      .catch((problem: unknown) => {
        setError(describeError(problem));
        setBusy(false);
      });
  }, [isLoaded, mode, signIn, signUp]);

  return { start, error, busy, isLoaded };
}
