/**
 * Turns whatever was thrown into one sentence a person can act on.
 *
 * Three sources of failure meet here — Clerk, our API, and the network — and
 * each reports differently. The auth screens should not each learn all three.
 */

import { isClerkAPIResponseError } from "@clerk/clerk-react/errors";
import { ApiError, NetworkError } from "./api";

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
