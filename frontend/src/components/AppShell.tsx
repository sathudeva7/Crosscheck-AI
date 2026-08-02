/**
 * The two-column frame: 238px sidebar, scrolling main.
 *
 * Copied from the prototype's `<aside>` — the width, the right hairline, the
 * surface background, the 16px/14px header padding, the 10px/8px nav padding,
 * the footer pinned with `margin-top:auto`.
 *
 * One thing works differently. The prototype flips a `tab` variable; these are
 * real routes. Back and forward behave, a refresh keeps your place, and a
 * finding can be linked to — which for a tool people will be shown by someone
 * else, or will come back to, is worth more than the state variable it saves.
 * The active styling is identical either way.
 */

import { NavLink, Outlet } from "react-router-dom";
import { useEffect, useState } from "react";
import type { CSSProperties, ReactNode } from "react";
import { LogoMark } from "./chrome";
import { useAuth } from "../lib/useAuth";
import { useReviewCount } from "../lib/useReviewCount";
import { listDocuments } from "../lib/documents";
import type { DocumentRead } from "../lib/documents";

/** The five destinations, in the prototype's order. */
const NAV: { to: string; label: string; icon: ReactNode }[] = [
  {
    to: "/app/timeline",
    label: "Timeline",
    icon: (
      <>
        <line x1="3" x2="21" y1="6" y2="6" />
        <line x1="3" x2="21" y1="12" y2="12" />
        <line x1="3" x2="21" y1="18" y2="18" />
      </>
    ),
  },
  {
    to: "/app/review",
    label: "Needs review",
    icon: (
      <>
        <path d="M9 11.5 11 13.5 15 9.5" />
        <path d="M21 12a9 9 0 1 1-9-9" />
        <path d="M16 3.13A4 4 0 0 1 22 7" />
      </>
    ),
  },
  {
    to: "/app/interactions",
    label: "Interactions",
    // No flag count yet. It belongs here, but it has to come from
    // `GET /findings` — a number invented by the sidebar would be a claim about
    // the user's medication safety that nothing checked.
    icon: (
      <>
        <path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z" />
        <path d="M12 9v4" />
        <path d="M12 17h.01" />
      </>
    ),
  },
  {
    to: "/app/trends",
    label: "Lab Trends",
    icon: (
      <>
        <path d="M3 3v16a2 2 0 0 0 2 2h16" />
        <path d="m19 9-5 5-4-4-3 3" />
      </>
    ),
  },
  {
    to: "/app/chat",
    label: "Ask CrossCheck",
    icon: <path d="M7.9 20A9 9 0 1 0 4 16.1L2 22Z" />,
  },
  {
    to: "/app/documents",
    label: "Documents",
    icon: (
      <>
        <path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z" />
        <path d="M14 2v4a2 2 0 0 0 2 2h4" />
      </>
    ),
  },
];

function NavIcon({ children, size = 17 }: { children: ReactNode; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      style={{ flex: "none" }}
      aria-hidden
    >
      {children}
    </svg>
  );
}

/** Up to two letters, from the first and last word of the name. */
function initialsOf(name: string | null | undefined): string {
  const words = (name ?? "").trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return "—";
  const first = words[0][0];
  const last = words.length > 1 ? words[words.length - 1][0] : "";
  return (first + last).toUpperCase();
}

export default function AppShell() {
  const { state, email, getToken } = useAuth();
  const { total: reviewCount, highRisk } = useReviewCount();
  const patientName = state.status === "ready" ? state.patient.full_name : null;
  const displayName = patientName ?? email ?? "Your records";

  // Read once when the shell mounts. The footer line is a summary, not a live
  // reading, and re-fetching it on every navigation would put a request in
  // front of every screen for a number that changes only on upload.
  const [documents, setDocuments] = useState<DocumentRead[] | null>(null);
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const found = await listDocuments(await getToken());
        if (!cancelled) setDocuments(found);
      } catch {
        // A failed count is not worth an error state in the sidebar; the
        // Documents tab reports the problem properly.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [getToken]);

  const providers = new Set(
    (documents ?? []).map((doc) => doc.provider).filter((name): name is string => Boolean(name)),
  ).size;

  return (
    <div style={{ flex: 1, display: "flex", minHeight: 0 }}>
      <aside
        style={{
          width: 238,
          flex: "none",
          borderRight: "1px solid var(--color-divider)",
          display: "flex",
          flexDirection: "column",
          background: "var(--color-surface)",
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 10,
            padding: "16px 16px 14px",
            borderBottom: "1px solid var(--color-divider)",
          }}
        >
          <LogoMark size={28} />
          <div style={{ fontFamily: "var(--font-heading)", fontWeight: 600, fontSize: 18 }}>
            CrossCheck
          </div>
        </div>

        <nav style={{ padding: "10px 8px", display: "flex", flexDirection: "column", gap: 2 }}>
          {NAV.map((item) => (
            <NavLink key={item.to} to={item.to} className="cc-nav" style={navStyle}>
              <NavIcon>{item.icon}</NavIcon>
              <span style={{ flex: 1, textAlign: "left" }}>{item.label}</span>
              {/* The only badge in the sidebar, and it is a real count of work
                  waiting — red when any of it is high risk. */}
              {item.to === "/app/review" && reviewCount > 0 && (
                <span
                  style={{
                    fontFamily: "var(--font-heading)",
                    fontWeight: 600,
                    fontSize: 11,
                    lineHeight: 1,
                    padding: "3px 7px",
                    background: highRisk > 0 ? "var(--color-danger)" : "var(--color-accent)",
                    color: "#fff",
                    flex: "none",
                  }}
                >
                  {reviewCount}
                </span>
              )}
            </NavLink>
          ))}
        </nav>

        <div
          style={{
            marginTop: "auto",
            padding: 14,
            borderTop: "1px solid var(--color-divider)",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 12 }}>
            <div
              style={{
                width: 34,
                height: 34,
                flex: "none",
                background: "var(--color-accent-200)",
                color: "var(--color-accent-800)",
                display: "grid",
                placeItems: "center",
                fontFamily: "var(--font-heading)",
                fontWeight: 600,
                fontSize: 15,
              }}
            >
              {initialsOf(patientName)}
            </div>
            <div style={{ minWidth: 0 }}>
              <div
                style={{
                  fontSize: 14,
                  fontWeight: 500,
                  whiteSpace: "nowrap",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                }}
                title={displayName}
              >
                {displayName}
              </div>
              <div style={{ fontSize: 11.5, color: "rgba(29,31,32,.5)" }}>
                {documents === null
                  ? " "
                  : documents.length === 0
                    ? "No documents yet"
                    : `${documents.length} record${documents.length === 1 ? "" : "s"}` +
                      (providers > 0
                        ? ` · ${providers} provider${providers === 1 ? "" : "s"}`
                        : "")}
              </div>
            </div>
          </div>

          <NavLink to="/app/documents" className="btn btn-secondary btn-block" style={{ marginTop: 0 }}>
            <NavIcon size={15}>
              <path d="M5 12h14" />
              <path d="M12 5v14" />
            </NavIcon>
            New analysis
          </NavLink>
        </div>
      </aside>

      <main className="cc-scroll" style={{ flex: 1, minWidth: 0, overflowY: "auto" }}>
        <div style={{ maxWidth: 1000, margin: "0 auto", padding: "28px 40px 60px" }}>
          <Outlet />
        </div>
      </main>
    </div>
  );
}

/** The active state changes four properties at once, which is why this is a
 *  function of `isActive` rather than a class. */
function navStyle({ isActive }: { isActive: boolean }): CSSProperties {
  return {
    display: "flex",
    alignItems: "center",
    gap: 11,
    padding: "9px 11px",
    border: "none",
    background: isActive ? "#eef6ff" : "transparent",
    color: isActive ? "#2c455d" : "#1d1f20",
    font: "inherit",
    fontSize: 14,
    cursor: "pointer",
    width: "100%",
    textAlign: "left",
    borderLeft: `2px solid ${isActive ? "#5980a6" : "transparent"}`,
    fontWeight: isActive ? 600 : 400,
    textDecoration: "none",
  };
}

