/**
 * Find a doctor for a flagged finding.
 *
 * The flow is the whole design: pick the issue, see which kind of clinician it
 * routes to and why, answer two questions, get real practices. Nothing here
 * invents a doctor — every row carries the directory it came from and a link
 * back to that listing, and a field the directory did not supply is printed as
 * "not published" rather than quietly omitted.
 *
 * Three things on this screen are load-bearing rather than decorative.
 *
 * The specialty options are *selectable*, not a headline with footnotes. The
 * app narrows the choice from every doctor in the country to two or three
 * sensible ones and then lets a person pick; presenting a single answer would
 * be claiming a certainty the routing does not have.
 *
 * The ladder is shown when it ran. A patient in Vavuniya sees "nothing within
 * 5, 10 or 25 km" alongside a doctor 31 km away, because a list of distant
 * doctors with no explanation reads as a bad search rather than as the honest
 * answer to a question with no closer one.
 *
 * The three directories are separate tabs that load independently. They
 * disagree in ways that matter — Vavuniya has no bookable cardiologist within
 * 30 km on one network, one 800 m away on another, and a government hospital
 * 200 m away that neither lists — and merging them would hide exactly that.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Corners } from "../components/chrome";
import ReportCard from "../components/ReportCard";
import { listDocuments } from "../lib/documents";
import type { DocumentRead } from "../lib/documents";
import { describeError } from "../lib/errors";
import { listFindings } from "../lib/findings";
import type { Finding } from "../lib/findings";
import {
  AVAILABILITY,
  PROVIDERS,
  RADII,
  distanceLabel,
  getSpecialties,
  hoursToday,
  initialsOf,
  searchProvider,
  suggestLocations,
} from "../lib/referrals";
import type {
  ProviderId,
  Referral,
  ReferralSearch,
  Specialty,
  SpecialtyList,
  Subject,
} from "../lib/referrals";
import { useAuth } from "../lib/useAuth";

/**
 * One selectable specialty, whichever route it arrived by.
 *
 * A finding's specialties come from the routing table and carry the
 * directory's own search words; a report's were chosen by reading the report,
 * and the name *is* the search word because it came out of the directory's own
 * catalogue. The picker does not need to know which — only what to show and
 * what to send back.
 */
interface Option {
  label: string;
  reason: string;
  confidence: string;
}

const asOption = (s: Specialty): Option => ({
  label: s.label,
  reason: s.reason,
  confidence: s.confidence,
});

const MUTED = "rgba(29,31,32,.6)";
const FAINT = "rgba(29,31,32,.45)";

const SEVERITY_LABEL: Record<string, { text: string; bg: string; fg: string }> = {
  danger: { text: "High risk", bg: "var(--color-danger-bg)", fg: "var(--color-danger-text)" },
  warning: { text: "Worth checking", bg: "var(--color-warn-bg)", fg: "var(--color-warn-text)" },
  info: { text: "For information", bg: "var(--color-accent-100)", fg: "var(--color-accent-800)" },
};

/** A pill in a row of choices. One of these is what every question here is. */
function Chip({
  label,
  selected,
  onClick,
  title,
}: {
  label: string;
  selected: boolean;
  onClick: () => void;
  title?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      aria-pressed={selected}
      style={{
        font: "inherit",
        fontSize: 13.5,
        padding: "7px 14px",
        cursor: "pointer",
        border: "1px solid",
        borderColor: selected ? "var(--color-accent)" : "var(--color-divider)",
        background: selected ? "var(--color-accent)" : "transparent",
        color: selected ? "var(--color-bg)" : "var(--color-text)",
      }}
    >
      {label}
    </button>
  );
}

function Label({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        fontSize: 10.5,
        letterSpacing: ".1em",
        textTransform: "uppercase",
        color: FAINT,
        marginBottom: 8,
      }}
    >
      {children}
    </div>
  );
}

/** The issue, and the kinds of clinician it routes to. */
function IssuePanel({
  finding,
  routing,
  chosen,
  onChoose,
}: {
  finding: Finding;
  routing: SpecialtyList | null;
  chosen: Option | null;
  onChoose: (s: Option) => void;
}) {
  const tone = SEVERITY_LABEL[finding.severity] ?? SEVERITY_LABEL.info;

  return (
    <div className="card blueprint" style={{ padding: "18px 20px", gap: 0 }}>
      <Corners />
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8 }}>
        <span
          style={{
            fontSize: 10.5,
            letterSpacing: ".07em",
            textTransform: "uppercase",
            padding: "3px 8px",
            background: tone.bg,
            color: tone.fg,
          }}
        >
          {tone.text}
        </span>
        <span style={{ fontFamily: "var(--font-heading)", fontSize: 19, lineHeight: 1.2 }}>
          {finding.title}
        </span>
      </div>

      <p style={{ margin: 0, fontSize: 14, color: MUTED, lineHeight: 1.55, maxWidth: 760 }}>
        {finding.detail}
      </p>

      <div style={{ height: 1, background: "var(--color-divider)", margin: "16px 0 14px" }} />

      {/* Under the finding rather than beside it: these are the next step in
          reading it, not a sidebar to it, and across the full width they can
          sit side by side where the choice between them is easier to see. */}
      <Label>Who to take this to</Label>
      {routing === null ? (
        <div style={{ fontSize: 13, color: FAINT }}>Working out who this should go to…</div>
      ) : (
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
        {routing.specialties.map(asOption).map((option) => {
          const selected = option.label === chosen?.label;
          return (
            <button
              key={option.label}
              type="button"
              onClick={() => onChoose(option)}
              aria-pressed={selected}
              style={{
                flex: "1 1 230px",
                minWidth: 210,
                font: "inherit",
                textAlign: "left",
                cursor: "pointer",
                padding: "10px 12px",
                border: "1px solid",
                borderColor: selected ? "var(--color-accent)" : "var(--color-divider)",
                background: selected
                  ? "color-mix(in srgb, var(--color-accent) 10%, transparent)"
                  : "transparent",
              }}
            >
              <div
                style={{
                  fontFamily: "var(--font-heading)",
                  fontSize: 16,
                  color: selected ? "var(--color-accent-800)" : "var(--color-text)",
                }}
              >
                {option.label}
              </div>
              <div style={{ fontSize: 12, color: MUTED, lineHeight: 1.45, marginTop: 2 }}>
                {option.reason}
              </div>
            </button>
          );
        })}
      </div>
      )}

      {routing && (
        <p
          style={{
            margin: "12px 0 0",
            fontSize: 11.5,
            color: FAINT,
            lineHeight: 1.45,
            maxWidth: 760,
          }}
        >
          {routing.disclaimer}
        </p>
      )}
    </div>
  );
}

/**
 * Type a few letters, pick a real place.
 *
 * Chosen rather than typed, because a free-text box accepts 'Vavunia' and
 * 'kandyy' — which geocode to nothing, and the screen then has to explain a
 * failure that was only ever a typo. Picking from the list guarantees the
 * search starts from somewhere that exists.
 *
 * Typing is still allowed: the suggestions are a help, not a gate, and a place
 * the directory has never heard of is still worth trying.
 */
function LocationPicker({
  value,
  onChange,
  onSubmit,
}: {
  value: string;
  onChange: (v: string) => void;
  onSubmit: () => void;
}) {
  const { getToken } = useAuth();
  const [options, setOptions] = useState<string[]>([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  // What the last accepted choice was, so re-opening the list on a value that
  // was picked rather than typed does not fight the user.
  const picked = useRef<string | null>(null);
  const box = useRef<HTMLDivElement | null>(null);

  // Debounced: one request per pause, not one per keystroke. Autocomplete is
  // billed per call, and 'Vavuniya' is eight of them typed out.
  useEffect(() => {
    if (picked.current === value || value.trim().length < 2) {
      setOptions([]);
      return;
    }
    let live = true;
    const timer = setTimeout(async () => {
      const found = await suggestLocations(value, await getToken());
      if (!live) return;
      setOptions(found);
      setOpen(found.length > 0);
      setActive(-1);
    }, 220);
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [value, getToken]);

  useEffect(() => {
    function away(event: MouseEvent) {
      if (box.current && !box.current.contains(event.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, []);

  const choose = (option: string) => {
    picked.current = option;
    onChange(option);
    setOpen(false);
    setOptions([]);
  };

  return (
    <div ref={box} style={{ position: "relative" }}>
      <input
        className="input"
        value={value}
        role="combobox"
        aria-expanded={open}
        aria-autocomplete="list"
        onChange={(event) => onChange(event.target.value)}
        onFocus={() => options.length > 0 && setOpen(true)}
        onKeyDown={(event) => {
          if (!open || options.length === 0) {
            if (event.key === "Enter") onSubmit();
            return;
          }
          if (event.key === "ArrowDown") {
            event.preventDefault();
            setActive((i) => (i + 1) % options.length);
          } else if (event.key === "ArrowUp") {
            event.preventDefault();
            setActive((i) => (i <= 0 ? options.length - 1 : i - 1));
          } else if (event.key === "Enter") {
            event.preventDefault();
            choose(active >= 0 ? options[active] : options[0]);
          } else if (event.key === "Escape") {
            setOpen(false);
          }
        }}
        placeholder="Start typing a town — e.g. Kan…"
      />
      {open && options.length > 0 && (
        <ul
          role="listbox"
          style={{
            position: "absolute",
            zIndex: 30,
            top: "100%",
            left: 0,
            right: 0,
            margin: "3px 0 0",
            padding: 0,
            listStyle: "none",
            background: "var(--color-bg)",
            border: "1px solid var(--color-divider)",
            boxShadow: "var(--shadow-md)",
            maxHeight: 230,
            overflowY: "auto",
          }}
        >
          {options.map((option, index) => (
            <li key={option}>
              <button
                type="button"
                role="option"
                aria-selected={index === active}
                onMouseEnter={() => setActive(index)}
                onClick={() => choose(option)}
                style={{
                  display: "block",
                  width: "100%",
                  textAlign: "left",
                  font: "inherit",
                  fontSize: 13.5,
                  padding: "8px 11px",
                  cursor: "pointer",
                  background:
                    index === active
                      ? "color-mix(in srgb, var(--color-accent) 12%, transparent)"
                      : "none",
                  border: 0,
                  borderBottom: "1px solid var(--color-divider)",
                  color: "var(--color-text)",
                }}
              >
                {option}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** The two questions, and how far to look. */
function Questions({
  location,
  setLocation,
  availability,
  setAvailability,
  radius,
  setRadius,
  onSearch,
  busy,
  ready,
}: {
  location: string;
  setLocation: (v: string) => void;
  availability: string;
  setAvailability: (v: string) => void;
  radius: number;
  setRadius: (v: number) => void;
  onSearch: () => void;
  busy: boolean;
  ready: boolean;
}) {
  return (
    <div className="card blueprint" style={{ padding: "18px 20px", gap: 18 }}>
      <Corners />
      <div style={{ display: "flex", gap: 30, flexWrap: "wrap" }}>
        <div style={{ flex: "1 1 320px", minWidth: 260 }}>
          <div style={{ fontSize: 13.5, marginBottom: 7 }}>1 · Where are you?</div>
          <LocationPicker value={location} onChange={setLocation} onSubmit={onSearch} />
          <div style={{ fontSize: 11.5, color: FAINT, marginTop: 6 }}>
            Pick a town or district — the search widens from there if nothing is close.
          </div>
        </div>

        <div style={{ flex: "1 1 320px", minWidth: 260 }}>
          <div style={{ fontSize: 13.5, marginBottom: 7 }}>2 · When are you free to go?</div>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            {AVAILABILITY.map((option) => (
              <Chip
                key={option}
                label={option}
                selected={availability === option}
                onClick={() => setAvailability(option)}
              />
            ))}
          </div>
          <div style={{ fontSize: 11.5, color: FAINT, marginTop: 6 }}>
            Used to flag practices open when you can go. Published hours only — always call first.
          </div>
        </div>
      </div>

      <div style={{ height: 1, background: "var(--color-divider)" }} />

      <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
        
        <button
          className="btn btn-primary"
          onClick={onSearch}
          disabled={busy || !ready || !location.trim()}
        >
          {busy ? "Searching…" : "Search real practices"}
        </button>
      </div>
    </div>
  );
}

/** How far the search had to look. Only shown when it had to widen. */
function Ladder({ search }: { search: ReferralSearch }) {
  const rungs = search.attempts;
  if (rungs.length < 2) return null;

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap", fontSize: 12 }}>
      <span style={{ color: FAINT }}>Looked within</span>
      {rungs.map((rung) => (
        <span
          key={`${rung.provider}-${rung.radius_km}`}
          style={{
            padding: "2px 7px",
            border: "1px solid var(--color-divider)",
            color: rung.found ? "var(--color-accent-800)" : FAINT,
            background: rung.found
              ? "color-mix(in srgb, var(--color-accent) 9%, transparent)"
              : "transparent",
          }}
        >
          {rung.radius_km} km · {rung.found}
        </span>
      ))}
    </div>
  );
}

/**
 * One labelled detail.
 *
 * A field the directory did not supply keeps its slot and says so, rather than
 * disappearing. A row missing its phone number and a row whose phone number was
 * never published look identical once the label is gone, and only one of those
 * is worth calling the hospital's main line about.
 */
function Field({
  label,
  value,
  missing,
}: {
  label: string;
  value?: React.ReactNode;
  missing: string;
}) {
  return (
    <div style={{ minWidth: 0 }}>
      <div
        style={{
          fontSize: 9.5,
          letterSpacing: ".11em",
          textTransform: "uppercase",
          color: FAINT,
          marginBottom: 5,
        }}
      >
        {label}
      </div>
      <div
        style={{
          fontSize: 13,
          lineHeight: 1.45,
          overflowWrap: "anywhere",
          color: value ? "var(--color-text)" : FAINT,
          fontStyle: value ? "normal" : "italic",
        }}
      >
        {value || missing}
      </div>
    </div>
  );
}

/**
 * One practice or doctor.
 *
 * Laid out as a record rather than a paragraph: an initialled tile and a rank
 * to tell rows apart at a glance, then every detail under its own label so the
 * gaps are as visible as the facts. What is *not* here is deliberate — there is
 * no walking time, because the distance is straight-line and a minutes figure
 * would imply a route we never asked for, and no match score, because the
 * ranking is distance and a directory's own word for the specialty rather than
 * anything that deserves a bar.
 */
function Row({ item, rank, origin }: { item: Referral; rank: number; origin: string }) {
  const distance = distanceLabel(item.distance_km);
  const isDoctor = item.kind === "doctor";
  const today = hoursToday(item.hours);
  const host = item.website?.replace(/^https?:\/\/(www\.)?/, "").replace(/\/+$/, "");
  // Doctors carry the same URL twice — the listing is the booking page — so the
  // second button would be the first one wearing a different label.
  const listing = item.source_url && item.source_url !== item.booking_url ? item.source_url : null;

  return (
    <div
      style={{
        display: "flex",
        gap: 16,
        padding: "18px 0",
        borderTop: "1px solid var(--color-divider)",
      }}
    >
      <div
        aria-hidden
        style={{
          flex: "none",
          width: 44,
          height: 44,
          display: "grid",
          placeItems: "center",
          border: "1px solid var(--color-divider)",
          fontFamily: "var(--font-heading)",
          fontSize: 15,
          color: MUTED,
        }}
      >
        {initialsOf(item.name)}
      </div>

      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: "flex", gap: 16, alignItems: "flex-start", flexWrap: "wrap" }}>
          <div style={{ flex: "1 1 260px", minWidth: 0 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 9, flexWrap: "wrap" }}>
              <span style={{ fontSize: 11.5, color: FAINT, fontVariantNumeric: "tabular-nums" }}>
                {String(rank).padStart(2, "0")}
              </span>
              <span style={{ fontFamily: "var(--font-heading)", fontSize: 17.5 }}>{item.name}</span>
              {!isDoctor && (
                <span
                  style={{
                    fontSize: 10,
                    letterSpacing: ".07em",
                    textTransform: "uppercase",
                    padding: "2px 7px",
                    border: "1px solid var(--color-divider)",
                    color: MUTED,
                  }}
                >
                  General listing
                </span>
              )}
              {item.open_now === true && (
                <span style={{ fontSize: 11.5, color: "var(--color-ok-text)" }}>Open now</span>
              )}
              {item.slmc_number && (
                <span style={{ fontSize: 11.5, color: MUTED }}>SLMC {item.slmc_number}</span>
              )}
            </div>
            <div style={{ fontSize: 13, color: MUTED, marginTop: 3, lineHeight: 1.45 }}>
              {item.specialty ||
                (isDoctor
                  ? "Specialty not published by the directory."
                  : "No specialty published. A general practice that can refer you on.")}
            </div>
          </div>

          <div style={{ flex: "none", textAlign: "right", minWidth: 110 }}>
            <div style={{ fontFamily: "var(--font-heading)", fontSize: 21, lineHeight: 1.1 }}>
              {distance ?? "—"}
            </div>
            <div style={{ fontSize: 11, color: FAINT, marginTop: 2 }}>
              {distance ? `from ${origin}` : "distance unknown"}
            </div>
            <div style={{ fontSize: 11.5, color: item.rating ? MUTED : FAINT, marginTop: 3 }}>
              {item.rating
                ? `${item.rating} ★${item.reviews ? ` (${item.reviews})` : ""}`
                : "No rating published"}
            </div>
          </div>
        </div>

        <div style={{ height: 1, background: "var(--color-divider)", margin: "13px 0" }} />

        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(158px, 1fr))",
            gap: "14px 22px",
          }}
        >
          {isDoctor && (
            <Field label="Channels at" value={item.clinic_name} missing="Not published" />
          )}
          <Field label="Address" value={item.address} missing="Not in the directory" />
          <Field
            label="Opening hours"
            value={today}
            missing="Not published — call to confirm"
          />
          <Field label="Phone" value={item.phone} missing="Not in the directory" />
          <Field
            label="Website"
            value={
              host && (
                <a href={item.website!} target="_blank" rel="noreferrer">
                  {host}
                </a>
              )
            }
            missing="Not in the directory"
          />
        </div>

        {item.other_clinics.length > 0 && (
          <div style={{ fontSize: 12, color: FAINT, marginTop: 12, lineHeight: 1.45 }}>
            Also channels at {item.other_clinics.slice(0, 3).join(", ")}
            {item.other_clinics.length > 3 && ` and ${item.other_clinics.length - 3} more`}
          </div>
        )}

        <div style={{ display: "flex", gap: 8, marginTop: 13, flexWrap: "wrap" }}>
          {item.booking_url && (
            <a
              className="btn btn-primary"
              href={item.booking_url}
              target="_blank"
              rel="noreferrer"
            >
              Book on {item.source === "doc990" ? "Doc990" : "eChannelling"}
            </a>
          )}
          {item.phone ? (
            <a
              className={item.booking_url ? "btn btn-secondary" : "btn btn-primary"}
              href={`tel:${item.phone.replace(/\s+/g, "")}`}
            >
              Call {item.phone}
            </a>
          ) : (
            <span style={{ fontSize: 12.5, color: FAINT, alignSelf: "center" }}>
              No number to call — the directory does not publish one
            </span>
          )}
          {listing && (
            <a className="btn btn-secondary" href={listing} target="_blank" rel="noreferrer">
              {item.source === "google_places" ? "Directions" : "View listing"}
            </a>
          )}
        </div>
      </div>
    </div>
  );
}

/** One directory's results, or its silence. */
function Results({
  search,
  loading,
  blurb,
  failure,
}: {
  search: ReferralSearch | null;
  loading: boolean;
  blurb: string;
  failure?: string;
}) {
  if (failure) {
    return (
      <div
        style={{
          margin: "16px 0 4px",
          fontSize: 13.5,
          padding: "10px 12px",
          background: "var(--color-warn-bg)",
          color: "var(--color-warn-text)",
          lineHeight: 1.5,
        }}
      >
        This directory could not be reached — {failure} The other tabs are unaffected.
      </div>
    );
  }
  if (loading) {
    return (
      <div style={{ padding: "26px 0", fontSize: 13.5, color: MUTED }}>
        Searching this directory…
      </div>
    );
  }
  if (!search) {
    return (
      <div style={{ padding: "26px 0", fontSize: 13.5, color: FAINT }}>
        Enter a location and search to see results.
      </div>
    );
  }

  return (
    <div style={{ paddingTop: 14 }}>
      <div style={{ fontSize: 12.5, color: MUTED, marginBottom: 10 }}>{blurb}</div>

      {search.degraded.map((line) => (
        <p
          key={line}
          style={{
            margin: "0 0 10px",
            fontSize: 13,
            padding: "8px 11px",
            background: "var(--color-warn-bg)",
            color: "var(--color-warn-text)",
          }}
        >
          {line}
        </p>
      ))}

      {search.notice && (
        <p style={{ margin: "0 0 10px", fontSize: 13.5, color: MUTED, lineHeight: 1.5 }}>
          {search.notice}
        </p>
      )}

      <Ladder search={search} />

      {search.results.length === 0 ? (
        <div style={{ padding: "18px 0", fontSize: 13.5, color: FAINT }}>
          Nothing found in this directory. That does not mean there is nobody — try another tab, or
          a nearby city.
        </div>
      ) : (
        <>
          <div style={{ marginTop: 12 }}>
            {search.results.map((item, index) => (
              <Row
                key={`${item.source}-${item.name}-${item.clinic_name ?? ""}`}
                item={item}
                rank={index + 1}
                origin={search.resolved_location ?? search.location}
              />
            ))}
          </div>
          <div
            style={{
              borderTop: "1px solid var(--color-divider)",
              paddingTop: 10,
              marginTop: 4,
              fontSize: 11.5,
              color: FAINT,
              lineHeight: 1.5,
            }}
          >
            {search.attribution.join(" · ")}
            {search.attribution.length > 0 && " — "}
            {search.disclaimer}
          </div>
        </>
      )}
    </div>
  );
}

/** The short label on an issue tab. The title, minus the grade in brackets. */
function chipLabel(finding: Finding): string {
  return finding.title.replace(/\s*\([^)]*\)\s*$/, "").trim() || finding.title;
}

/** Which kind of thing the reader is finding a doctor for. */
type Source = "findings" | "reports";

export default function FindDoctorPage() {
  const { getToken } = useAuth();

  const [source, setSource] = useState<Source>("findings");

  const [findings, setFindings] = useState<Finding[] | null>(null);
  const [findingId, setFindingId] = useState<string | null>(null);
  const [reports, setReports] = useState<DocumentRead[] | null>(null);
  const [documentId, setDocumentId] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  const finding = useMemo(
    () => findings?.find((f) => f.id === findingId) ?? null,
    [findings, findingId],
  );

  // What every search below is *for*. A finding and a lab report reach the
  // same directories by different endpoints, and this is the only place the
  // difference is decided.
  const subject: Subject | null = useMemo(() => {
    if (source === "findings") return findingId ? { kind: "finding", id: findingId } : null;
    return documentId ? { kind: "document", id: documentId } : null;
  }, [source, findingId, documentId]);

  const [routing, setRouting] = useState<SpecialtyList | null>(null);
  const [chosen, setChosen] = useState<Option | null>(null);
  const [location, setLocation] = useState("");
  const [availability, setAvailability] = useState(AVAILABILITY[0]);
  const [radius, setRadius] = useState(10);
  const [tab, setTab] = useState<ProviderId>("echannelling");

  const [results, setResults] = useState<Partial<Record<ProviderId, ReferralSearch>>>({});
  const [failed, setFailed] = useState<Partial<Record<ProviderId, string>>>({});
  const [loading, setLoading] = useState<Partial<Record<ProviderId, boolean>>>({});
  const [searched, setSearched] = useState(false);
  // A search that has been superseded must not paint over a newer one.
  const run = useRef(0);

  // Both lists at once. Which tab opens depends on what the patient has, and
  // that is not known until both have answered — someone whose only reason to
  // see a doctor is a lab report should not land on an empty Issues tab.
  useEffect(() => {
    let live = true;
    (async () => {
      try {
        const token = await getToken();
        const [list, docs] = await Promise.all([listFindings(token), listDocuments(token)]);
        if (!live) return;
        const labs = docs.filter((d) => d.doc_type === "lab_report");
        setFindings(list.findings);
        setReports(labs);
        setFindingId((current) => current ?? list.findings[0]?.id ?? null);
        setDocumentId((current) => current ?? labs[0]?.id ?? null);
        if (list.findings.length === 0 && labs.length > 0) setSource("reports");
      } catch (error) {
        if (live) setProblem(describeError(error));
      }
    })();
    return () => {
      live = false;
    };
  }, [getToken]);

  // Who this issue goes to. Needs no location, so it loads with the issue
  // rather than waiting for the reader to answer anything. Reports take the
  // other path: their specialties were decided at upload and arrive with the
  // card, so there is nothing to fetch here.
  useEffect(() => {
    setResults({});
    setFailed({});
    setSearched(false);
    setChosen(null);
    if (source !== "findings" || !findingId) {
      setRouting(null);
      return;
    }
    let live = true;
    setRouting(null);
    (async () => {
      try {
        const found = await getSpecialties(findingId, await getToken());
        if (!live) return;
        setRouting(found);
        setChosen(found.specialties[0] ? asOption(found.specialties[0]) : null);
      } catch (error) {
        if (live) setProblem(describeError(error));
      }
    })();
    return () => {
      live = false;
    };
  }, [source, findingId, documentId, getToken]);

  const search = useCallback(async () => {
    if (!location.trim() || !subject || !chosen) return;
    const ticket = ++run.current;
    const token = await getToken();

    setSearched(true);
    setResults({});
    setFailed({});
    setLoading({ echannelling: true, doc990: true, google_places: true });

    // Fired together and painted as each lands. The directories answer at very
    // different speeds — one gateway takes seconds where Google takes under
    // two — and waiting for the slowest before showing anything would hold the
    // whole screen back for the tab the reader may not even open.
    for (const provider of PROVIDERS) {
      searchProvider(
        subject,
        provider.id,
        {
          location,
          specialty: chosen.label,
          availability,
          radius_km: radius,
        },
        token,
      )
        .then((found) => {
          if (run.current !== ticket) return;
          setResults((prev) => ({ ...prev, [provider.id]: found }));
        })
        .catch((error) => {
          if (run.current !== ticket) return;
          // One directory failing is not the search failing. The tab says so
          // and the other two are unaffected.
          setFailed((prev) => ({ ...prev, [provider.id]: describeError(error) }));
        })
        .finally(() => {
          if (run.current !== ticket) return;
          setLoading((prev) => ({ ...prev, [provider.id]: false }));
        });
    }
  }, [location, subject, chosen, availability, radius, getToken]);

  const count = (id: ProviderId) => results[id]?.results.length;

  if (problem) {
    return (
      <div className="card blueprint" style={{ padding: "18px 20px" }}>
        <Corners />
        <p style={{ margin: 0, fontSize: 14 }}>{problem}</p>
      </div>
    );
  }

  if (findings === null || reports === null) {
    return <div style={{ padding: "26px 0", fontSize: 13.5, color: MUTED }}>Loading…</div>;
  }

  if (findings.length === 0 && reports.length === 0) {
    return (
      <div className="card blueprint" style={{ padding: "18px 20px", gap: 6 }}>
        <Corners />
        <h4 style={{ margin: 0 }}>Nothing to refer yet</h4>
        <p style={{ margin: 0, fontSize: 14, color: MUTED, maxWidth: 560 }}>
          This screen finds a doctor for an issue the cross-checks flagged, or for a lab report.
          Upload a prescription or a lab report to get started.
        </p>
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20, paddingBottom: 40 }}>
      <div>
        <div
          style={{
            fontSize: 10.5,
            letterSpacing: ".12em",
            textTransform: "uppercase",
            color: "var(--color-accent)",
          }}
        >
          Referral support
        </div>
        <h2 style={{ margin: "4px 0 6px" }}>Find a doctor nearby</h2>
        <p style={{ margin: 0, fontSize: 14, color: MUTED, maxWidth: 640, lineHeight: 1.55 }}>
          CrossCheck matches the flagged issue to a kind of clinician, then searches public
          directories for real practices near you. It does not diagnose, and it never invents a
          clinic.
        </p>
      </div>

      {/* Two ways in, because two different things send someone looking for a
          doctor. Most lab reports never raise a finding — a creatinine 0.04
          below range and a result reported as a word both flag nothing — so
          without the second tab the whole of lab-driven referral would be
          unreachable from this screen. */}
      <div style={{ display: "flex", gap: 0, borderBottom: "1px solid var(--color-divider)" }}>
        {([
          ["findings", "Flagged issues", findings.length],
          ["reports", "Lab reports", reports.length],
        ] as const).map(([id, label, count]) => {
          const active = source === id;
          return (
            <button
              key={id}
              type="button"
              onClick={() => setSource(id)}
              aria-pressed={active}
              disabled={count === 0}
              style={{
                font: "inherit",
                fontFamily: "var(--font-heading)",
                fontSize: 15,
                cursor: count === 0 ? "default" : "pointer",
                padding: "9px 15px",
                background: "none",
                border: 0,
                borderBottom: "2px solid",
                borderBottomColor: active ? "var(--color-accent)" : "transparent",
                color: count === 0 ? FAINT : active ? "var(--color-accent-800)" : MUTED,
                marginBottom: -1,
              }}
            >
              {label} <span style={{ fontSize: 12, color: FAINT }}>{count}</span>
            </button>
          );
        })}
      </div>

      {source === "findings" ? (
        <>
          <div>
            <Label>Issue being referred</Label>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              {findings.map((item) => (
                <Chip
                  key={item.id}
                  label={chipLabel(item)}
                  selected={item.id === findingId}
                  onClick={() => setFindingId(item.id)}
                />
              ))}
            </div>
          </div>

          {finding && (
            <IssuePanel
              finding={finding}
              routing={routing}
              chosen={chosen}
              onChoose={setChosen}
            />
          )}
        </>
      ) : (
        <>
          <div>
            <Label>Report being referred</Label>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              {reports.map((item) => (
                <Chip
                  key={item.id}
                  label={item.filename || "Lab report"}
                  selected={item.id === documentId}
                  onClick={() => setDocumentId(item.id)}
                />
              ))}
            </div>
          </div>

          {/* The card is the picker here. Its 'Find one' buttons choose the
              specialty, so the reader reads the report and acts on it in one
              place rather than being shown a summary and then asked the same
              question again underneath it. */}
          {documentId && (
            <ReportCard
              key={documentId}
              documentId={documentId}
              onFindDoctor={(specialty, reason, confidence) =>
                setChosen({ label: specialty, reason, confidence })
              }
              chosenSpecialty={chosen?.label ?? null}
            />
          )}
        </>
      )}

      <Questions
        location={location}
        setLocation={setLocation}
        availability={availability}
        setAvailability={setAvailability}
        radius={radius}
        setRadius={setRadius}
        onSearch={search}
        busy={Object.values(loading).some(Boolean)}
        ready={Boolean(chosen)}
      />

      {searched && (
        <div className="card blueprint" style={{ padding: "16px 20px", gap: 0 }}>
          <Corners />
          <div
            style={{
              display: "flex",
              alignItems: "baseline",
              gap: 12,
              flexWrap: "wrap",
              marginBottom: 12,
            }}
          >
            <span style={{ fontFamily: "var(--font-heading)", fontSize: 20 }}>
              {chosen?.label ?? "Doctors"} near {location}
            </span>
            <span style={{ fontSize: 12.5, color: FAINT }}>
              Availability: {availability.toLowerCase()}
            </span>
          </div>

          <div style={{ display: "flex", gap: 0, borderBottom: "1px solid var(--color-divider)" }}>
            {PROVIDERS.map((provider) => {
              const active = provider.id === tab;
              const found = count(provider.id);
              return (
                <button
                  key={provider.id}
                  type="button"
                  onClick={() => setTab(provider.id)}
                  aria-pressed={active}
                  style={{
                    font: "inherit",
                    fontFamily: "var(--font-heading)",
                    fontSize: 15,
                    cursor: "pointer",
                    padding: "9px 15px",
                    background: "none",
                    border: 0,
                    borderBottom: "2px solid",
                    borderBottomColor: active ? "var(--color-accent)" : "transparent",
                    color: active ? "var(--color-accent-800)" : MUTED,
                    marginBottom: -1,
                  }}
                >
                  {provider.label}
                  {loading[provider.id] ? (
                    <span style={{ color: FAINT, fontSize: 12 }}> …</span>
                  ) : failed[provider.id] ? (
                    <span style={{ color: "var(--color-warn-text)", fontSize: 12 }}> !</span>
                  ) : found !== undefined ? (
                    <span style={{ color: FAINT, fontSize: 12 }}> {found}</span>
                  ) : null}
                </button>
              );
            })}
          </div>

          <Results
            search={results[tab] ?? null}
            loading={Boolean(loading[tab])}
            failure={failed[tab]}
            blurb={PROVIDERS.find((p) => p.id === tab)?.blurb ?? ""}
          />
        </div>
      )}
    </div>
  );
}
