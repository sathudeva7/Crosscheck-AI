/**
 * Routes and guards.
 *
 * Every guard reads the same `AuthState`, so there is one answer to "where does
 * this user belong" rather than one per screen. The `loading` case is the part
 * that matters: Clerk restores the session asynchronously and the patient
 * profile is a network call, so for the first moments after a page load nobody
 * knows whether the user is signed in. Deciding then would bounce a signed-in
 * user to /login on every refresh.
 *
 *   loading      -> hold
 *   signed-out   -> /login
 *   needs-setup  -> /setup   (Clerk knows them, the API has no patient row)
 *   ready        -> /app
 *
 * The third state is the backend's own: `current_patient` answers 404 rather
 * than 401 precisely so this distinction can be made.
 */

import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import type { ReactNode } from "react";
import { SafetyStrip, Splash } from "./components/chrome";
import { useAuth } from "./lib/useAuth";
import { ReviewCountProvider } from "./lib/useReviewCount";
import { landingRoute } from "./lib/auth";
import AppShell from "./components/AppShell";
import ComingSoonPage from "./pages/ComingSoonPage";
import LoginPage from "./pages/LoginPage";
import ProcessingPage from "./pages/ProcessingPage";
import RegisterPage from "./pages/RegisterPage";
import ReviewPage from "./pages/ReviewPage";
import SetupPage from "./pages/SetupPage";
import SsoCallbackPage from "./pages/SsoCallbackPage";
import TimelinePage from "./pages/TimelinePage";
import UploadPage from "./pages/UploadPage";
import { SSO_CALLBACK_PATH } from "./lib/useGoogle";

/** Signed in, whatever the profile situation. */
function RequireAuth({ children }: { children: ReactNode }) {
  const { state } = useAuth();
  const location = useLocation();

  if (state.status === "loading") return <Splash />;
  if (state.status === "signed-out") {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  return <>{children}</>;
}

/** Signed in *and* holding a patient profile. */
function RequireSetup({ children }: { children: ReactNode }) {
  const { state } = useAuth();
  if (state.status === "needs-setup") return <Navigate to="/setup" replace />;
  return <>{children}</>;
}

/**
 * Setup is a one-time step: the API rejects a second `POST /patients` with a
 * 409, so someone who already has a profile is sent on rather than shown a form
 * that can only fail.
 *
 * On to Documents rather than the timeline, which is also what completing setup
 * does — the button says "Continue to upload" and this guard is what carries it
 * out, so the redirect lives in one place instead of racing a `navigate()` in
 * the page. A fresh account has an empty timeline anyway; the useful screen is
 * the one that asks for a document.
 */
function RequireNoProfile({ children }: { children: ReactNode }) {
  const { state } = useAuth();
  if (state.status === "ready") return <Navigate to="/app/documents" replace />;
  return <>{children}</>;
}

/** Signed-in users have no business on the sign-in screen. */
function RedirectIfSignedIn({ children }: { children: ReactNode }) {
  const { state } = useAuth();
  if (state.status === "loading") return <Splash />;
  if (state.status !== "signed-out") return <Navigate to={landingRoute(state)} replace />;
  return <>{children}</>;
}

/** `/` and anything unrecognised: send the visitor where they belong. */
function Landing() {
  const { state } = useAuth();
  if (state.status === "loading") return <Splash />;
  return <Navigate to={landingRoute(state)} replace />;
}

export default function App() {
  return (
    // `height` rather than `min-height`, and the overflow hidden with it: the
    // sidebar has to stay put while the main pane scrolls, which is what the
    // prototype's `overflow-y:auto` and its styled scrollbar are for. With
    // min-height the frame grows instead and the whole page scrolls, taking
    // the sidebar with it. Each pane below owns its own scrolling.
    <div style={{ height: "100vh", overflow: "hidden", display: "flex", flexDirection: "column" }}>
      {/* Outside the router on purpose: the disclaimer shows on every screen,
          including the auth ones, and cannot be navigated away from. */}
      <SafetyStrip />

      <Routes>
        {/* Unguarded, and it has to be: mid-exchange the user is neither
            signed in nor signed out, so any guard here would redirect away
            from the URL carrying the code. */}
        <Route path={SSO_CALLBACK_PATH} element={<SsoCallbackPage />} />

        <Route
          path="/login"
          element={
            <RedirectIfSignedIn>
              <LoginPage />
            </RedirectIfSignedIn>
          }
        />
        <Route
          path="/register"
          element={
            <RedirectIfSignedIn>
              <RegisterPage />
            </RedirectIfSignedIn>
          }
        />
        <Route
          path="/setup"
          element={
            <RequireAuth>
              <RequireNoProfile>
                <SetupPage />
              </RequireNoProfile>
            </RequireAuth>
          }
        />
        {/* Full-screen rather than a tab: while this runs there is nothing in
            the shell worth navigating to yet. */}
        <Route
          path="/processing"
          element={
            <RequireAuth>
              <RequireSetup>
                <ProcessingPage />
              </RequireSetup>
            </RequireAuth>
          }
        />

        <Route
          path="/app"
          element={
            <RequireAuth>
              <RequireSetup>
                {/* Inside the guards, so the count is never fetched for a user
                    who has no patient row to count against. */}
                <ReviewCountProvider>
                  <AppShell />
                </ReviewCountProvider>
              </RequireSetup>
            </RequireAuth>
          }
        >
          {/* Timeline is the app's home: it is the thing every other tab is a
              view onto, so an empty /app belongs there. */}
          <Route index element={<Navigate to="/app/timeline" replace />} />
          <Route path="timeline" element={<TimelinePage />} />
          <Route path="review" element={<ReviewPage />} />
          <Route
            path="interactions"
            element={
              <ComingSoonPage
                eyebrow="Safety cross-check"
                title="Flagged findings"
                description="Drug interactions, duplicate prescriptions and conflicting doses found across every document — each with its sources and a confidence score."
              />
            }
          />
          <Route
            path="trends"
            element={
              <ComingSoonPage
                eyebrow="Results over time"
                title="Lab trends"
                description="Each measurement plotted across every report that contains it, with the direction of travel explained in plain language."
              />
            }
          />
          <Route
            path="chat"
            element={
              <ComingSoonPage
                eyebrow="Follow-up questions"
                title="Ask CrossCheck"
                description="Questions answered by reasoning across all of your documents at once, with the sources each answer came from."
              />
            }
          />
          <Route path="documents" element={<UploadPage />} />
          {/* An unknown tab is a mistyped URL, not a 404 worth a screen. */}
          <Route path="*" element={<Navigate to="/app/timeline" replace />} />
        </Route>
        <Route path="*" element={<Landing />} />
      </Routes>
    </div>
  );
}
