/**
 * Patient setup.
 *
 * Built from the prototype's setup screen — the 560px column, the 28px/26px
 * card padding, the segmented control, the 2:1 name/date row, the consent box
 * with its 18px square, the 46px button — but the question it asks has changed.
 * One account is one patient, always themselves, so there is no "whose records
 * are these": the profile is the account holder's, and the form collects what
 * the patient table actually stores.
 *
 * Behind it this is a real `POST /patients`, which is what turns an
 * authenticated Clerk user into a patient row the rest of the API can find.
 * Until it exists `current_patient` answers 404 and no document, timeline or
 * finding can be loaded — so this step is a gate, not a formality.
 *
 * The consent statement is the reason it is a gate. The tool reads medical
 * documents and flags interactions, and the user has to have said, in their own
 * action, that they understand it is not a diagnosis — before they are shown a
 * single finding.
 */

import { useEffect, useState } from "react";
import type React from "react";
import { AuthHeader, AuthShell, CheckSquare, Corners, FieldError } from "../components/chrome";
import { useAuth, useSubmit } from "../lib/useAuth";
import type { Sex } from "../lib/auth";

/**
 * The four values the `patient_sex_check` constraint allows, in the words a
 * person would use. "Prefer not to say" is not a refusal to answer — it stores
 * `unknown`, which is exactly what the reference-range logic needs to be told
 * so it does not quietly apply the wrong band.
 */
const SEXES: { value: Sex; label: string }[] = [
  { value: "female", label: "Female" },
  { value: "male", label: "Male" },
  { value: "other", label: "Other" },
  // Short on purpose: four options share one 560px row, and "Prefer not to
  // say" is the width at which the labels start wrapping.
  { value: "unknown", label: "Not stated" },
];

export default function SetupPage() {
  const { email, providerName, loadError, saveProfile, refresh, signOut } = useAuth();
  const [fullName, setFullName] = useState(providerName ?? "");
  const [dateOfBirth, setDateOfBirth] = useState("");
  const [sex, setSex] = useState<Sex | null>(null);
  const [description, setDescription] = useState("");
  const [consent, setConsent] = useState(false);
  const [nameTouched, setNameTouched] = useState(false);
  const { error, busy, submit } = useSubmit();

  // Google gives us a name, so asking the user to retype it would be careless.
  // It arrives a beat after this screen mounts, hence the effect rather than
  // just an initial value — and it stops the moment they edit the field.
  useEffect(() => {
    if (!nameTouched && providerName) setFullName(providerName);
  }, [providerName, nameTouched]);

  const ready = fullName.trim().length > 0 && consent;

  function onSubmit(event: React.SyntheticEvent) {
    event.preventDefault();
    void submit(async () => {
      await saveProfile(
        {
          // Clerk already holds a verified address; sending it keeps the
          // patient row self-contained for anything that reads it server-side.
          email,
          full_name: fullName.trim(),
          // Empty string is not a date, and blank text is not a description.
          // Both go as null so the nullable columns receive the right thing
          // and nothing downstream has to treat "" as absent.
          dob: dateOfBirth || null,
          sex,
          description: description.trim() || null,
        },
        { consentedAt: new Date().toISOString() },
      );
      // No navigate(): the state machine moves to `ready` and the router
      // follows. One rule for landing, not two.
    });
  }

  return (
    <AuthShell>
      <AuthHeader step="Step 2 of 2 · Set up" />

      {/* A failed lookup is not the same as "no profile yet", so it is shown
          here with a way out rather than silently becoming a blank form. */}
      {loadError && (
        <div className="card blueprint" style={{ padding: "16px 18px", marginBottom: 14 }}>
          <Corners />
          <FieldError>{loadError}</FieldError>
          <button
            type="button"
            className="btn btn-secondary"
            style={{ marginTop: 10 }}
            onClick={() => void refresh()}
          >
            Try again
          </button>
        </div>
      )}

      <form className="card blueprint" style={{ padding: "28px 26px", gap: 0 }} onSubmit={onSubmit}>
        <Corners />

        <h2 style={{ margin: "0 0 6px", fontSize: 28 }}>About you</h2>
        <p
          style={{
            color: "color-mix(in srgb, var(--color-text) 60%, transparent)",
            marginBottom: 22,
          }}
        >
          Everything you upload is reconciled under this one profile, so the timeline and flags
          stay tied to the right person.
        </p>

        <div style={{ display: "flex", gap: 14, marginBottom: 6, flexWrap: "wrap" }}>
          <div className="field" style={{ flex: 2, minWidth: 200 }}>
            <label htmlFor="fullName">Full name</label>
            <input
              id="fullName"
              className="input"
              placeholder="e.g. Ravi Kumar"
              value={fullName}
              onChange={(e) => {
                setNameTouched(true);
                setFullName(e.target.value);
              }}
            />
          </div>
          <div className="field" style={{ flex: 1, minWidth: 130 }}>
            <label htmlFor="dob">
              Date of birth <span style={{ opacity: 0.5 }}>(optional)</span>
            </label>
            <input
              id="dob"
              className="input"
              type="date"
              value={dateOfBirth}
              onChange={(e) => setDateOfBirth(e.target.value)}
            />
          </div>
        </div>

        <div className="field" style={{ marginBottom: 6 }}>
          <label>
            Sex <span style={{ opacity: 0.5 }}>(optional)</span>
          </label>
          {/* No `htmlFor` above, because the control is a group rather than one
              input — this is what tells a screen reader the same thing. */}
          <div className="seg" role="radiogroup" aria-label="Sex" style={{ width: "100%" }}>
            {SEXES.map((option) => (
              <label
                key={option.value}
                className="seg-opt"
                style={{ flex: 1, justifyContent: "center" }}
              >
                <input
                  type="radio"
                  name="sex"
                  checked={sex === option.value}
                  onChange={() => setSex(option.value)}
                />
                {option.label}
              </label>
            ))}
          </div>
        </div>

        {/* Neither field is required, so the app has to say why it asks for
            them at all — this is the honest reason, and it is the difference
            between a lab value reading as normal or as flagged. */}
        <p
          style={{
            fontSize: 12,
            lineHeight: 1.5,
            color: "color-mix(in srgb, var(--color-text) 50%, transparent)",
            margin: "0 0 18px",
          }}
        >
          Lab reference ranges depend on age and sex. Without these, results are compared against
          the range printed on the report only.
        </p>

        <div className="field" style={{ marginBottom: 18 }}>
          <label htmlFor="description">
            Medical background <span style={{ opacity: 0.5 }}>(optional)</span>
          </label>
          <textarea
            id="description"
            className="input"
            placeholder="Ongoing conditions, known allergies, long-term medication — anything a pharmacist would want to know."
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
          <div
            style={{
              fontSize: 11.5,
              marginTop: 5,
              color: "color-mix(in srgb, var(--color-text) 50%, transparent)",
            }}
          >
            Used as context when cross-checking your documents. You can change it later.
          </div>
        </div>

        {/* The whole block is the hit target, not just the square. */}
        <div
          role="checkbox"
          aria-checked={consent}
          tabIndex={0}
          onClick={() => setConsent((on) => !on)}
          onKeyDown={(e) => {
            if (e.key === " " || e.key === "Enter") {
              e.preventDefault();
              setConsent((on) => !on);
            }
          }}
          style={{
            display: "flex",
            alignItems: "flex-start",
            gap: 11,
            cursor: "pointer",
            padding: "12px 13px",
            border: "1px solid var(--color-divider)",
            background: "var(--color-surface)",
          }}
        >
          <CheckSquare checked={consent} />
          <span
            style={{
              fontSize: 13,
              lineHeight: 1.5,
              color: "color-mix(in srgb, var(--color-text) 78%, transparent)",
            }}
          >
            I understand CrossCheck reconciles documents for information only and does{" "}
            <strong>not</strong> provide a medical diagnosis. I'll confirm any finding with a
            licensed clinician.
          </span>
        </div>

        <FieldError>{error}</FieldError>

        <button
          className="btn btn-primary btn-block"
          style={{ marginTop: 20, height: 46, fontSize: 16 }}
          disabled={!ready || busy}
          type="submit"
        >
          {busy ? "Saving…" : "Continue to upload →"}
        </button>

        <p
          style={{
            textAlign: "center",
            fontSize: 12,
            color: "color-mix(in srgb, var(--color-text) 45%, transparent)",
            margin: "10px 0 0",
          }}
        >
          Signed in as {email ?? "your account"} ·{" "}
          <button type="button" className="btn-link" onClick={() => void signOut()}>
            Not you?
          </button>
        </p>
      </form>
    </AuthShell>
  );
}
