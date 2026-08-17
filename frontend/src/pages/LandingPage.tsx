/**
 * The public page — what CrossCheck is, for someone who has never seen it.
 *
 * Built from the same blueprint tokens as the app rather than a marketing
 * style of its own: square edges, hairline borders on transparent surfaces,
 * registration marks at the corners of every panel. A landing page that looked
 * like a different product would be the first thing a visitor distrusted, and
 * this one is asking them to upload their prescriptions.
 *
 * The icons are Health Icons (healthicons.org), CC0 — an icon set drawn for
 * public-health software rather than a general illustration library, so a
 * blister pack is a blister pack and a cardiogram is a cardiogram. See
 * `../assets/healthicons/LICENCE.md`.
 *
 * Every number on this page is real and was counted from the running system —
 * 215,382 interaction rules, 1,737 drugs, the two channelling directories'
 * own specialty catalogues. Nothing here is rounded up for effect. An app
 * whose entire argument is "we do not invent medical data" cannot open with an
 * invented statistic.
 *
 * The safety line is not in the footer. It sits above the fold, in the hero,
 * because "this is not a diagnosis" is a condition of using the thing rather
 * than a disclaimer to be found afterwards.
 */

import { Link } from "react-router-dom";
import type { ReactNode } from "react";
import { Corners, LogoMark, Wordmark } from "../components/chrome";
import HealthIcon from "../components/HealthIcon";
import type { HealthIconName } from "../components/HealthIcon";

const MUTED = "rgba(29,31,32,.62)";
const FAINT = "rgba(29,31,32,.45)";
const WIDTH = 1080;

/** Faint square grid — the drafting paper the whole look is named after. */
const BLUEPRINT_GRID: React.CSSProperties = {
  backgroundImage:
    "linear-gradient(rgba(89,128,166,.07) 1px, transparent 1px)," +
    "linear-gradient(90deg, rgba(89,128,166,.07) 1px, transparent 1px)",
  backgroundSize: "34px 34px",
};

function Section({ children, tint = false }: { children: ReactNode; tint?: boolean }) {
  return (
    <section
      style={{
        borderTop: "1px solid var(--color-divider)",
        background: tint ? "var(--color-surface)" : "transparent",
      }}
    >
      <div style={{ maxWidth: WIDTH, margin: "0 auto", padding: "58px 32px" }}>{children}</div>
    </section>
  );
}

function Eyebrow({ children }: { children: ReactNode }) {
  return (
    <div
      style={{
        fontSize: 10.5,
        letterSpacing: ".14em",
        textTransform: "uppercase",
        color: "var(--color-accent)",
        marginBottom: 10,
      }}
    >
      {children}
    </div>
  );
}

function Heading({ children, size = 30 }: { children: ReactNode; size?: number }) {
  return (
    <h2
      style={{
        margin: 0,
        fontFamily: "var(--font-heading)",
        fontWeight: 600,
        fontSize: size,
        lineHeight: 1.16,
        letterSpacing: "-.01em",
      }}
    >
      {children}
    </h2>
  );
}

function Lede({ children }: { children: ReactNode }) {
  return (
    <p style={{ margin: "12px 0 0", fontSize: 15, lineHeight: 1.6, color: MUTED, maxWidth: 620 }}>
      {children}
    </p>
  );
}

/** A bordered panel with the registration marks the whole app is drawn with. */
function Panel({ children, style }: { children: ReactNode; style?: React.CSSProperties }) {
  return (
    <div className="card blueprint" style={{ padding: "18px 20px", gap: 8, ...style }}>
      <Corners />
      {children}
    </div>
  );
}

/** The bordered square an icon sits in, so the glyphs line up down the page. */
function IconWell({ name, size = 40 }: { name: HealthIconName; size?: number }) {
  return (
    <span
      style={{
        width: size,
        height: size,
        display: "grid",
        placeItems: "center",
        border: "1px solid var(--color-divider)",
        background: "var(--color-bg)",
        flex: "none",
      }}
    >
      <HealthIcon name={name} size={Math.round(size * 0.62)} />
    </span>
  );
}

function Step({
  n,
  title,
  body,
  icon,
}: {
  n: string;
  title: string;
  body: string;
  icon: HealthIconName;
}) {
  return (
    <Panel style={{ flex: "1 1 232px", minWidth: 210 }}>
      <IconWell name={icon} />
      <div style={{ display: "flex", alignItems: "baseline", gap: 9, marginTop: 4 }}>
        <span
          style={{
            fontFamily: "var(--font-heading)",
            fontSize: 12,
            color: "var(--color-accent)",
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {n}
        </span>
        <span style={{ fontFamily: "var(--font-heading)", fontSize: 17 }}>{title}</span>
      </div>
      <p style={{ margin: 0, fontSize: 13.5, lineHeight: 1.55, color: MUTED }}>{body}</p>
    </Panel>
  );
}

function Check({ title, body, icon }: { title: string; body: string; icon: HealthIconName }) {
  return (
    <div style={{ flex: "1 1 300px", minWidth: 260, display: "flex", gap: 13 }}>
      <IconWell name={icon} size={34} />
      <div>
        <div style={{ fontFamily: "var(--font-heading)", fontSize: 16, marginBottom: 2 }}>
          {title}
        </div>
        <p style={{ margin: 0, fontSize: 13.5, lineHeight: 1.55, color: MUTED }}>{body}</p>
      </div>
    </div>
  );
}

function Stat({ value, label }: { value: string; label: string }) {
  return (
    <div style={{ flex: "1 1 168px", minWidth: 140 }}>
      <div
        style={{
          fontFamily: "var(--font-heading)",
          fontSize: 30,
          lineHeight: 1,
          letterSpacing: "-.01em",
        }}
      >
        {value}
      </div>
      <div style={{ fontSize: 12.5, color: MUTED, marginTop: 5, lineHeight: 1.45 }}>{label}</div>
    </div>
  );
}

export default function LandingPage() {
  return (
    <div className="cc-scroll" style={{ flex: 1, overflowY: "auto", background: "var(--color-bg)" }}>
      {/* --- top bar --- */}
      <div
        style={{
          borderBottom: "1px solid var(--color-divider)",
          background: "var(--color-bg)",
          position: "sticky",
          top: 0,
          zIndex: 20,
        }}
      >
        <div
          style={{
            maxWidth: WIDTH,
            margin: "0 auto",
            padding: "13px 32px",
            display: "flex",
            alignItems: "center",
            gap: 11,
          }}
        >
          <LogoMark size={30} />
          <Wordmark size={19} />
          <div style={{ flex: 1 }} />
          <Link className="btn btn-secondary" to="/login">
            Sign in
          </Link>
          <Link className="btn btn-primary" to="/register">
            Get started
          </Link>
        </div>
      </div>

      {/* --- hero --- */}
      <div style={BLUEPRINT_GRID}>
        <div style={{ maxWidth: WIDTH, margin: "0 auto", padding: "64px 32px 58px" }}>
          <div style={{ display: "flex", gap: 44, flexWrap: "wrap", alignItems: "flex-start" }}>
            <div style={{ flex: "1 1 460px", minWidth: 300 }}>
              <Eyebrow>Medical report &amp; prescription cross-checker</Eyebrow>
              <h1
                style={{
                  margin: 0,
                  fontFamily: "var(--font-heading)",
                  fontWeight: 600,
                  fontSize: 46,
                  lineHeight: 1.08,
                  letterSpacing: "-.015em",
                  maxWidth: 560,
                }}
              >
                Every prescription you have been given, checked against all the others.
              </h1>
              <p
                style={{
                  margin: "18px 0 0",
                  fontSize: 16,
                  lineHeight: 1.62,
                  color: MUTED,
                  maxWidth: 540,
                }}
              >
                You see one doctor for your heart and another for your stomach. Neither of them
                has the other's prescription in front of them. CrossCheck reads all of your
                documents — photos, scans, PDFs — puts them on one timeline, and checks them
                against each other.
              </p>

              <div style={{ display: "flex", gap: 10, marginTop: 26, flexWrap: "wrap" }}>
                <Link className="btn btn-primary" to="/register">
                  Upload your first document
                </Link>
                <Link className="btn btn-secondary" to="/login">
                  I already have an account
                </Link>
              </div>

              {/* Above the fold on purpose. This is a condition of using the
                  app, not a disclaimer to be discovered later. */}
              <p style={{ margin: "20px 0 0", fontSize: 12.5, color: FAINT, maxWidth: 500 }}>
                An informational cross-check, not a medical diagnosis. Always confirm anything
                you find here with a licensed doctor or pharmacist.
              </p>
            </div>

            {/* A real finding, in the shape the app draws it. */}
            <Panel style={{ flex: "1 1 340px", minWidth: 300, maxWidth: 430, gap: 0 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 9, marginBottom: 9 }}>
                <span
                  style={{
                    fontSize: 10,
                    letterSpacing: ".07em",
                    textTransform: "uppercase",
                    padding: "3px 8px",
                    background: "var(--color-warn-bg)",
                    color: "var(--color-warn-text)",
                  }}
                >
                  Worth checking
                </span>
                <span style={{ fontSize: 11.5, color: FAINT }}>Moderate</span>
                <span style={{ marginLeft: "auto" }}>
                  <HealthIcon name="interactions" size={22} />
                </span>
              </div>
              <div style={{ fontFamily: "var(--font-heading)", fontSize: 19, lineHeight: 1.25 }}>
                Cimetidine and Metoprolol may interact
              </div>
              <p style={{ margin: "9px 0 0", fontSize: 13.5, lineHeight: 1.6, color: MUTED }}>
                Cimetidine was started on 12 March by one doctor. Metoprolol has been taken since
                January, prescribed by another. Neither prescription mentions the other.
              </p>

              <div
                style={{ height: 1, background: "var(--color-divider)", margin: "15px 0 12px" }}
              />

              <div style={{ display: "flex", flexWrap: "wrap", gap: 7 }}>
                {["Pharmacist", "Cardiologist", "General physician"].map((who) => (
                  <span
                    key={who}
                    style={{
                      fontSize: 12,
                      padding: "4px 9px",
                      border: "1px solid var(--color-divider)",
                      color: MUTED,
                    }}
                  >
                    {who}
                  </span>
                ))}
              </div>
              <div style={{ fontSize: 11.5, color: FAINT, marginTop: 10 }}>
                Every finding names who can review it — and finds one near you.
              </div>
            </Panel>
          </div>
        </div>
      </div>

      {/* --- how it works --- */}
      <Section tint>
        <Eyebrow>How it works</Eyebrow>
        <Heading>Four steps, and you keep the paperwork</Heading>
        <Lede>
          Photograph a prescription, drop in a PDF of lab results, or upload a discharge summary.
          Handwriting included.
        </Lede>
        <div style={{ display: "flex", gap: 14, flexWrap: "wrap", marginTop: 28 }}>
          <Step
            n="01"
            title="Upload"
            icon="upload"
            body="Photos, scans and PDFs. Each page is read, classified and transcribed — including handwritten prescriptions."
          />
          <Step
            n="02"
            title="Verify"
            icon="verify"
            body="Anything read with low confidence is queued for you to confirm. You correct it once, and the correction sticks."
          />
          <Step
            n="03"
            title="Cross-check"
            icon="crosscheck"
            body="Every medicine is identified against RxNorm and the WHO drug classification, then checked against every other one on your record."
          />
          <Step
            n="04"
            title="Act"
            icon="referral"
            body="Each finding says who can review it, and finds real doctors near you who can be booked today."
          />
        </div>
      </Section>

      {/* --- what it checks --- */}
      <Section>
        <Eyebrow>What it looks for</Eyebrow>
        <Heading>Six checks across your whole record, not one document</Heading>
        <Lede>
          A prescription is safe on its own and unsafe beside one written three years ago by
          someone else. Adding a document today can raise a finding about an old one.
        </Lede>
        <div style={{ display: "flex", gap: 26, flexWrap: "wrap", marginTop: 28 }}>
          <Check
            title="Drug interactions"
            icon="interactions"
            body="Checked against 215,382 published interaction rules, with the severity each one carries."
          />
          <Check
            title="Duplicate therapy"
            icon="duplicate"
            body="Two medicines doing the same job under different brand names, from two different doctors."
          />
          <Check
            title="Dosage conflicts"
            icon="dosage"
            body="The same medicine prescribed twice at once, or a dose that changed without the old one stopping."
          />
          <Check
            title="Allergy conflicts"
            icon="allergy"
            body="A medicine related to something already recorded on your chart as a reaction."
          />
          <Check
            title="Lab trends"
            icon="trends"
            body="A result drifting the wrong way across several tests, which no single report shows."
          />
          <Check
            title="Critical values"
            icon="critical"
            body="A single result far enough outside its normal range to act on now."
          />
        </div>
      </Section>

      {/* --- the honesty section --- */}
      <Section tint>
        <div style={{ display: "flex", gap: 40, flexWrap: "wrap" }}>
          <div style={{ flex: "1 1 420px", minWidth: 300 }}>
            <Eyebrow>Real data, or none</Eyebrow>
            <Heading>Nothing here is invented</Heading>
            <Lede>
              Every fact traces back to a page of your own document, with the line it was read
              from. Every doctor is fetched live from a directory you can open yourself and check
              the name and phone number against.
            </Lede>
            <p
              style={{
                margin: "14px 0 0",
                fontSize: 15,
                lineHeight: 1.6,
                color: MUTED,
                maxWidth: 620,
              }}
            >
              No doctor, clinic or appointment is ever stored. A clinic that closed last week
              stops being suggested, because the question is asked at the moment you ask it.
            </p>
          </div>

          <Panel style={{ flex: "1 1 320px", minWidth: 280, maxWidth: 400, gap: 0 }}>
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 9,
                marginBottom: 12,
              }}
            >
              <HealthIcon name="sources" size={20} />
              <span
                style={{
                  fontSize: 10.5,
                  letterSpacing: ".1em",
                  textTransform: "uppercase",
                  color: FAINT,
                }}
              >
                Where the answers come from
              </span>
            </div>
            {[
              ["RxNorm & WHO ATC", "identifying and classifying every medicine"],
              ["DDInter 2.0", "published drug interaction rules"],
              ["openFDA", "label text when no rule exists"],
              ["eChannelling & Doc990", "doctors you can book, read live"],
              ["Google Maps", "government hospitals neither directory lists"],
            ].map(([name, what], i) => (
              <div
                key={name}
                style={{
                  padding: "9px 0",
                  borderTop: i === 0 ? "none" : "1px solid var(--color-divider)",
                }}
              >
                <div style={{ fontSize: 13.5, fontWeight: 500 }}>{name}</div>
                <div style={{ fontSize: 12.5, color: MUTED, marginTop: 1 }}>{what}</div>
              </div>
            ))}
          </Panel>
        </div>
      </Section>

      {/* --- numbers --- */}
      <Section>
        <div style={{ display: "flex", gap: 26, flexWrap: "wrap" }}>
          <Stat value="215,382" label="interaction rules checked against" />
          <Stat value="1,737" label="medicines in the reference set" />
          <Stat value="1,372" label="bookable specialties, read live" />
          <Stat value="3" label="doctor directories searched at once" />
        </div>
      </Section>

      {/* --- closing --- */}
      <Section tint>
        <div style={{ display: "flex", gap: 30, flexWrap: "wrap", alignItems: "center" }}>
          <div style={{ flex: "1 1 440px", minWidth: 300 }}>
            <Heading size={26}>Start with one prescription</Heading>
            <Lede>
              You do not need a full record for it to be useful. Upload what you have, and add to
              it the next time you see a doctor.
            </Lede>
          </div>
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
            <Link className="btn btn-primary" to="/register">
              Get started
            </Link>
            <Link className="btn btn-secondary" to="/login">
              Sign in
            </Link>
          </div>
        </div>
      </Section>

      <footer style={{ borderTop: "1px solid var(--color-divider)" }}>
        <div
          style={{
            maxWidth: WIDTH,
            margin: "0 auto",
            padding: "22px 32px 40px",
            display: "flex",
            gap: 14,
            flexWrap: "wrap",
            alignItems: "center",
          }}
        >
          <LogoMark size={24} />
          <span style={{ fontSize: 12.5, color: MUTED, flex: "1 1 420px" }}>
            CrossCheck — an informational cross-check of your own medical documents. Not a
            diagnosis, and not a substitute for a doctor or pharmacist.
          </span>
          {/* CC0 asks for nothing. Credited anyway: a volunteer project this
              page leans on is worth a line. */}
          <span style={{ fontSize: 11.5, color: FAINT }}>
            Icons by{" "}
            <a href="https://healthicons.org" target="_blank" rel="noreferrer">
              Health Icons
            </a>{" "}
            (CC0)
          </span>
        </div>
      </footer>
    </div>
  );
}
