/**
 * Turns whatever was thrown into one sentence a person can act on.
 *
 * Three sources of failure meet here — Clerk, our API, and the network — and
 * each reports differently. The auth screens should not each learn all three.
 */

import { isClerkAPIResponseError } from "@clerk/clerk-react/errors";
import { ApiError, NetworkError } from "./api";

/**
 * Just enough of Clerk's sign-in attempt to explain one.
 *
 * Structural rather than imported: `@clerk/types` is not a direct dependency,
 * and reaching through the react package's internals for a type would break on
 * an upgrade that reorganises them. A real `SignInResource` satisfies this.
 */
interface IncompleteSignIn {
  status: string | null;
  supportedFirstFactors?: readonly { strategy: string }[] | null;
  supportedSecondFactors?: readonly { strategy: string }[] | null;
}

/** How an OAuth strategy reads to a person: 'oauth_google' -> 'Google'. */
function providerName(strategy: string): string {
  const name = strategy.replace(/^oauth_/, "").replace(/_/g, " ");
  return name.charAt(0).toUpperCase() + name.slice(1);
}

/**
 * Why a sign-in that did not fail still did not finish.
 *
 * Clerk answers `signIn.create` with a status rather than an error when the
 * attempt is valid but incomplete, and there are several such statuses. This
 * used to collapse all of them into one sentence — "this account needs an
 * extra verification step that is not set up yet" — which named a cause it had
 * not checked and blamed the account for what is usually our configuration.
 *
 * The full attempt is logged as well. A status alone is not always enough to
 * tell a missing password credential from a browser that could not hold a
 * session, and the difference decides who has to fix it.
 */
export function describeIncompleteSignIn(attempt: IncompleteSignIn): string {
  const strategies = new Set<string>(
    (attempt.supportedFirstFactors ?? []).map((factor) => factor.strategy),
  );

  console.error("Sign-in did not complete", {
    status: attempt.status,
    firstFactors: [...strategies],
    secondFactors: (attempt.supportedSecondFactors ?? []).map((factor) => factor.strategy),
  });

  switch (attempt.status) {
    case "needs_first_factor": {
      // The common real cause: the account was created through Google and has
      // no password to check. Only said when password is genuinely absent from
      // what Clerk offers — otherwise this would be a guess of its own.
      if (!strategies.has("password")) {
        const oauth = [...strategies].filter((s) => s.startsWith("oauth_")).map(providerName);
        if (oauth.length > 0) {
          return `This account signs in with ${oauth.join(" or ")}. Use the button below instead of a password.`;
        }
        if (strategies.has("email_code")) {
          return "This account signs in with an emailed code rather than a password.";
        }
      }
      return `Sign-in could not be completed in this browser (${attempt.status}). Third-party cookies may be blocked.`;
    }
    case "needs_second_factor":
      return "This account has two-factor authentication turned on, which this app cannot complete yet.";
    case "needs_new_password":
      return "This password has expired and must be reset before signing in.";
    case "needs_identifier":
      return "Clerk did not receive the email address. Please try again.";
    default:
      return `Sign-in did not complete (${attempt.status}). Please try again.`;
  }
}

export function describeError(problem: unknown): string {
  if (isClerkAPIResponseError(problem)) {
    // Clerk returns an array; the first is the one about the field the user
    // just touched. `longMessage` is the sentence, `message` is the label.
    const first = problem.errors[0];
    return first?.longMessage ?? first?.message ?? "That did not work. Try again.";
  }

  if (problem instanceof NetworkError) return problem.message;

  if (problem instanceof ApiError) {
    // 409 is the one status worth rewording: it means the profile already
    // exists, which is not an error the user caused or can fix by retrying.
    if (problem.status === 409) return "This account already has a profile.";
    if (problem.status === 401) return "Your session expired. Sign in again.";
    return problem.message;
  }

  return problem instanceof Error ? problem.message : "Something went wrong.";
}
