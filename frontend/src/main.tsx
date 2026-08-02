import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { ClerkProvider } from "@clerk/clerk-react";
import { AuthHeader, AuthShell, Corners, SafetyStrip } from "./components/chrome";
import { AuthProvider } from "./lib/useAuth";
import App from "./App";
import "./styles/industry.css";

const PUBLISHABLE_KEY = import.meta.env.VITE_CLERK_PUBLISHABLE_KEY;

/**
 * Shown instead of the app when the Clerk key is missing.
 *
 * ClerkProvider throws without one, which in a browser means a blank page and
 * a console message — so this says what to do instead, in the app's own
 * language rather than a stack trace.
 */
function MissingKey() {
  return (
    <div style={{ minHeight: "100vh", display: "flex", flexDirection: "column" }}>
      <SafetyStrip />
      <AuthShell width={520}>
        <AuthHeader step="Setup required" />
        <div className="card blueprint" style={{ padding: "28px 26px", gap: 0 }}>
          <Corners />
          <h2 style={{ margin: "0 0 6px", fontSize: 28 }}>Clerk key missing</h2>
          <p style={{ color: "color-mix(in srgb, var(--color-text) 60%, transparent)" }}>
            Sign-in needs a publishable key from your Clerk dashboard.
          </p>
          <ol style={{ margin: "18px 0 0", paddingLeft: 20, fontSize: 14, lineHeight: 1.9 }}>
            <li>
              Open <strong>dashboard.clerk.com</strong> → API keys.
            </li>
            <li>
              Copy the <strong>Publishable key</strong> (<code>pk_test_…</code>).
            </li>
            <li>
              Create <code>frontend/.env.local</code> with:
              <pre
                style={{
                  margin: "8px 0 0",
                  padding: "10px 12px",
                  background: "var(--color-surface)",
                  border: "1px solid var(--color-divider)",
                  fontSize: 12.5,
                  overflowX: "auto",
                }}
              >
                VITE_CLERK_PUBLISHABLE_KEY=pk_test_…
              </pre>
            </li>
            <li>Restart the dev server — Vite reads env files only at startup.</li>
          </ol>
        </div>
      </AuthShell>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    {PUBLISHABLE_KEY ? (
      // afterSignOutUrl keeps sign-out landing on our own screen rather than
      // Clerk's hosted page.
      <ClerkProvider publishableKey={PUBLISHABLE_KEY} afterSignOutUrl="/login">
        <BrowserRouter>
          <AuthProvider>
            <App />
          </AuthProvider>
        </BrowserRouter>
      </ClerkProvider>
    ) : (
      <MissingKey />
    )}
  </StrictMode>,
);
