/**
 * Placeholder for the tabs not built yet.
 *
 * Every sidebar destination resolves to something, because a nav item that
 * silently does nothing reads as a bug. Each of these says what the screen will
 * do, which also keeps the plan visible while the rest is built.
 */

import { Corners } from "../components/chrome";

interface Props {
  eyebrow: string;
  title: string;
  description: string;
}

export default function ComingSoonPage({ eyebrow, title, description }: Props) {
  return (
    <div className="cc-rise">
      <h6 style={{ color: "var(--color-accent)", marginBottom: 4 }}>{eyebrow}</h6>
      <h2 style={{ margin: "0 0 6px", fontSize: 30 }}>{title}</h2>
      <p style={{ color: "rgba(29,31,32,.6)", maxWidth: "64ch", marginBottom: 22 }}>
        {description}
      </p>

      <div className="card blueprint" style={{ padding: "22px 24px" }}>
        <Corners />
        <div
          style={{
            fontSize: 13,
            letterSpacing: ".08em",
            textTransform: "uppercase",
            fontFamily: "var(--font-heading)",
            color: "color-mix(in srgb, var(--color-text) 45%, transparent)",
          }}
        >
          Not built yet
        </div>
      </div>
    </div>
  );
}
