/**
 * React binding for the two halves of identity.
 *
 * Clerk supplies the session; this provider adds the second question the app
 * actually needs answered — does a patient profile exist yet — and folds both
 * into the single `AuthState` the router reads.
 *
 * The profile is fetched once per signed-in user and then held. Refetching on
 * every navigation would put a request in front of every screen for an answer
 * that changes exactly once, at setup.
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { useAuth as useClerkAuth, useUser } from "@clerk/clerk-react";
import { createPatient, fetchPatient } from "./auth";
import type { AccountMetadata, AuthState, Patient, PatientDraft } from "./auth";
import { describeError } from "./errors";

interface AuthValue {
  state: AuthState;
  /** Clerk's email for the account, used to prefill and to send to the API. */
  email: string | null;
  /** Name from the identity provider, when there is one. Google supplies it;
   *  email sign-up does not, since this app never asks for it. */
  providerName: string | null;
  metadata: AccountMetadata;
  /** Non-null when the profile lookup failed for a reason other than 404. */
  loadError: string | null;
  /** Creates the patient row and stores consent. Resolves once state is `ready`. */
  saveProfile: (draft: PatientDraft, metadata: AccountMetadata) => Promise<Patient>;
  /** Re-reads `/patients/me`; used to recover after a failed lookup. */
  refresh: () => Promise<void>;
  /** The Clerk session token, for calls this context does not make itself.
   *  Fetched per call rather than held, because Clerk rotates it. */
  getToken: () => Promise<string | null>;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const { isLoaded: clerkLoaded, isSignedIn, userId, getToken, signOut } = useClerkAuth();
  const { user } = useUser();

  const [patient, setPatient] = useState<Patient | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  // Which user the `patient` above belongs to. Null means "not looked up yet",
  // and it is what keeps the state machine in `loading` until the answer lands.
  const [loadedFor, setLoadedFor] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!userId) return;
    try {
      setPatient(await fetchPatient(await getToken()));
      setLoadError(null);
    } catch (problem) {
      // Deliberately not treated as "no profile": sending the user to setup
      // after a network failure would only fail again on submit, with a
      // confusing message. Report it instead.
      setPatient(null);
      setLoadError(describeError(problem));
    } finally {
      setLoadedFor(userId);
    }
  }, [userId, getToken]);

  useEffect(() => {
    if (!clerkLoaded) return;

    if (!isSignedIn) {
      setPatient(null);
      setLoadError(null);
      setLoadedFor(null);
      return;
    }

    if (loadedFor === userId) return;

    let cancelled = false;
    void (async () => {
      const token = await getToken();
      if (cancelled) return;
      try {
        const found = await fetchPatient(token);
        if (!cancelled) {
          setPatient(found);
          setLoadError(null);
        }
      } catch (problem) {
        if (!cancelled) {
          setPatient(null);
          setLoadError(describeError(problem));
        }
      } finally {
        if (!cancelled) setLoadedFor(userId ?? null);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [clerkLoaded, isSignedIn, userId, loadedFor, getToken]);

  const email = user?.primaryEmailAddress?.emailAddress ?? null;
  // `fullName` is null unless a first name is set, which for this app means it
  // is set only when the account came from Google.
  const providerName = user?.fullName ?? null;
  const metadata = (user?.unsafeMetadata ?? {}) as AccountMetadata;

  const state = useMemo<AuthState>(() => {
    if (!clerkLoaded) return { status: "loading" };
    if (!isSignedIn) return { status: "signed-out" };
    if (loadedFor !== userId) return { status: "loading" };
    return patient ? { status: "ready", patient } : { status: "needs-setup" };
  }, [clerkLoaded, isSignedIn, userId, loadedFor, patient]);

  const saveProfile = useCallback(
    async (draft: PatientDraft, extra: AccountMetadata) => {
      const created = await createPatient(await getToken(), draft);

      // Consent is recorded after the row exists, so a failure here cannot
      // leave a profile marked as consented that was never created. Merged
      // rather than replaced: Clerk metadata is shared with anything else that
      // may come to use it.
      await user?.update({ unsafeMetadata: { ...(user.unsafeMetadata ?? {}), ...extra } });

      setPatient(created);
      setLoadError(null);
      setLoadedFor(userId ?? null);
      return created;
    },
    [getToken, user, userId],
  );

  const value = useMemo<AuthValue>(
    () => ({
      state,
      email,
      providerName,
      metadata,
      loadError,
      saveProfile,
      refresh: load,
      getToken: () => getToken(),
      signOut: async () => {
        await signOut();
      },
    }),
    [state, email, providerName, metadata, loadError, saveProfile, load, getToken, signOut],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside <AuthProvider>");
  return value;
}

/**
 * Wraps an async form submit so a thrown error becomes a message under the
 * form rather than an unhandled rejection, and the button stays disabled while
 * the request is in flight. Every auth screen submits the same way, so the
 * handling belongs here rather than in each of them.
 */
export function useSubmit() {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = useCallback(async (action: () => Promise<void> | void) => {
    setError(null);
    setBusy(true);
    try {
      await action();
    } catch (problem) {
      setError(describeError(problem));
    } finally {
      setBusy(false);
    }
  }, []);

  return { error, busy, submit, clearError: useCallback(() => setError(null), []) };
}
