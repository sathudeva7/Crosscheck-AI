/**
 * The pieces that repeat on every screen.
 *
 * The safety strip in particular is not decoration: the competition brief
 * requires the tool never present itself as a diagnosis, and a banner that
 * lives outside the router — above every page, including the auth screens —
 * is the only version of that promise a user cannot navigate away from.
 */

import type { ReactNode } from "react";

/** The four registration marks that make a box read as a blueprint object. */
export function Corners() {
  return (
    <>
      <i className="corner tl" />
      <i className="corner tr" />
      <i className="corner bl" />
      <i className="corner br" />
    </>
  );
}

export function SafetyStrip() {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 10,
        padding: "8px 20px",
        background: "var(--color-accent-900)",
        color: "#cfe0f0",
        fontSize: 12.5,
        letterSpacing: ".01em",
        flex: "none",
      }}
    >
      <svg
        width="15"
        height="15"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
        style={{ flex: "none", color: "var(--color-caution)" }}
        aria-hidden
      >
        <path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z" />
        <path d="M12 9v4" />
        <path d="M12 17h.01" />
      </svg>
      <span>
        <strong style={{ color: "#fff", fontFamily: "var(--font-heading)" }}>
          Informational cross-check — not a medical diagnosis.
        </strong>{" "}
        Always confirm findings with a licensed doctor or pharmacist before acting.
      </span>
    </div>
  );
}

/** The pill-and-flask mark, sized to whatever square it sits in. */
export function LogoMark({ size = 34 }: { size?: number }) {
  return (
    <div
      style={{
        width: size,
        height: size,
        background: "var(--color-accent)",
        display: "grid",
        placeItems: "center",
        flex: "none",
      }}
    >
      <svg
        width={Math.round(size * 0.59)}
        height={Math.round(size * 0.59)}
        viewBox="0 0 24 24"
        fill="none"
        stroke="#fff"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden
      >
        <path d="M11 2v2" />
        <path d="M5 2v2" />
        <path d="M5 3H4a2 2 0 0 0-2 2v4a6 6 0 0 0 12 0V5a2 2 0 0 0-2-2h-1" />
        <path d="M8 15a6 6 0 0 0 12 0v-3" />
        <circle cx="20" cy="10" r="2" />
      </svg>
    </div>
  );
}

export function Wordmark({ size = 22 }: { size?: number }) {
  return (
    <div
      style={{
        fontFamily: "var(--font-heading)",
        fontWeight: 600,
        fontSize: size,
        letterSpacing: "-.01em",
      }}
    >
      CrossCheck
    </div>
  );
}

/**
 * The header row shared by every auth screen: mark, wordmark, and a step
 * label pushed to the right.
 */
export function AuthHeader({ step }: { step?: string }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 22 }}>
      <LogoMark />
      <Wordmark />
      {step && (
        <span
          style={{
            marginLeft: "auto",
            fontSize: 12,
            letterSpacing: ".08em",
            textTransform: "uppercase",
            color: "color-mix(in srgb, var(--color-text) 50%, transparent)",
          }}
        >
          {step}
        </span>
      )}
    </div>
  );
}

/**
 * The centred column every auth screen sits in. `width` is the only thing
 * that varies between them — 560px for setup, 460px for sign in.
 */
export function AuthShell({ children, width = 560 }: { children: ReactNode; width?: number }) {
  return (
    <div
      // Scrolls itself. The frame around it is fixed at viewport height so the
      // app's sidebar can stay put, which means every pane inside has to own
      // its own overflow — including this one, or a short window clips the
      // setup form.
      className="cc-rise cc-scroll"
      style={{
        flex: 1,
        overflowY: "auto",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        padding: "52px 24px 72px",
      }}
    >
      <div style={{ width: "100%", maxWidth: width }}>{children}</div>
    </div>
  );
}

/**
 * A hairline rule with a word sitting in it. Separates the social route from
 * the email one without adding a heading neither of them needs.
 */
export function OrDivider({ label = "or" }: { label?: string }) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 12,
        margin: "18px 0",
        color: "color-mix(in srgb, var(--color-text) 42%, transparent)",
        fontSize: 11.5,
        letterSpacing: ".1em",
        textTransform: "uppercase",
      }}
    >
      <span style={{ flex: 1, height: 1, background: "var(--color-divider)" }} />
      {label}
      <span style={{ flex: 1, height: 1, background: "var(--color-divider)" }} />
    </div>
  );
}

/**
 * Continue with Google.
 *
 * Google's brand guidelines describe a white pill with their own typeface, and
 * dropping that into a blueprint interface would read as a piece of someone
 * else's product. The mark is theirs, unaltered and in its own colours — which
 * is the part that actually identifies the service — and the button around it
 * is ours.
 */
export function GoogleButton({
  label,
  onClick,
  disabled,
}: {
  label: string;
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      className="btn btn-secondary btn-block"
      style={{ height: 46, fontSize: 15, gap: 10 }}
      onClick={onClick}
      disabled={disabled}
    >
      <svg width="17" height="17" viewBox="0 0 48 48" aria-hidden style={{ flex: "none" }}>
        <path
          fill="#4285F4"
          d="M45.12 24.5c0-1.56-.14-3.06-.4-4.5H24v8.51h11.84c-.51 2.75-2.06 5.08-4.39 6.64v5.52h7.11c4.16-3.83 6.56-9.47 6.56-16.17z"
        />
        <path
          fill="#34A853"
          d="M24 46c5.94 0 10.92-1.97 14.56-5.33l-7.11-5.52c-1.97 1.32-4.49 2.1-7.45 2.1-5.73 0-10.58-3.87-12.31-9.07H4.34v5.7C7.96 41.07 15.4 46 24 46z"
        />
        <path
          fill="#FBBC05"
          d="M11.69 28.18C11.25 26.86 11 25.45 11 24s.25-2.86.69-4.18v-5.7H4.34C2.85 17.09 2 20.45 2 24s.85 6.91 2.34 9.88l7.35-5.7z"
        />
        <path
          fill="#EA4335"
          d="M24 10.75c3.23 0 6.13 1.11 8.41 3.29l6.31-6.31C34.91 4.18 29.93 2 24 2 15.4 2 7.96 6.93 4.34 14.12l7.35 5.7c1.73-5.2 6.58-9.07 12.31-9.07z"
        />
      </svg>
      {label}
    </button>
  );
}

/**
 * Held while Clerk restores the session and the patient profile is fetched.
 *
 * Quiet on purpose. This shows for a few hundred milliseconds on a cold load,
 * and a spinner that size reads as "something is wrong" rather than "wait".
 */
export function Splash({ label = "Loading" }: { label?: string }) {
  return (
    <div
      style={{
        flex: 1,
        display: "grid",
        placeItems: "center",
        padding: 24,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 12, opacity: 0.65 }}>
        <LogoMark size={26} />
        <span
          style={{
            fontFamily: "var(--font-heading)",
            fontSize: 15,
            letterSpacing: ".06em",
            textTransform: "uppercase",
          }}
        >
          {label}
        </span>
      </div>
    </div>
  );
}

/** Checkbox drawn in the system's language: a square, filled when checked. */
export function CheckSquare({ checked }: { checked: boolean }) {
  return (
    <span
      style={{
        width: 18,
        height: 18,
        flex: "none",
        border: "1.5px solid var(--color-accent)",
        display: "grid",
        placeItems: "center",
        marginTop: 1,
        background: checked ? "var(--color-accent)" : "transparent",
      }}
    >
      {checked && (
        <svg
          width="13"
          height="13"
          viewBox="0 0 24 24"
          fill="none"
          stroke="#fff"
          strokeWidth="3"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden
        >
          <path d="M20 6 9 17l-5-5" />
        </svg>
      )}
    </span>
  );
}

/** A field-level error, in the danger colour rather than the accent. */
export function FieldError({ children }: { children: ReactNode }) {
  if (!children) return null;
  return (
    <div
      role="alert"
      style={{
        display: "flex",
        alignItems: "center",
        gap: 7,
        marginTop: 8,
        padding: "8px 11px",
        background: "var(--color-danger-bg)",
        color: "var(--color-danger-text)",
        fontSize: 13,
        borderLeft: "2px solid var(--color-danger)",
      }}
    >
      <svg
        width="14"
        height="14"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
        style={{ flex: "none" }}
        aria-hidden
      >
        <circle cx="12" cy="12" r="10" />
        <path d="M12 8v4" />
        <path d="M12 16h.01" />
      </svg>
      <span>{children}</span>
    </div>
  );
}
