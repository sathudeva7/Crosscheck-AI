/**
 * Demo data — the prototype's, verbatim.
 *
 * This file is scaffolding with a known end: every export here is replaced by a
 * call to the real API, and the components that read it do not change when that
 * happens. Nothing else in the app should import from a file named `demo`, so
 * the day it is deleted the compiler lists exactly what still depends on it.
 *
 * The five visits below are deliberately a worked example rather than filler.
 * They contain the two findings the backend actually detects — warfarin against
 * ibuprofen, and amoxicillin against a recorded penicillin allergy — so the
 * timeline shows what the tool is for before a single document is uploaded.
 */

/** A file in the sample set, as the upload screen lists it. */
export interface DemoDocument {
  id: string;
  name: string;
  provider: string;
  date: string;
  type: string;
  pages: number;
}

export const DEMO_DOCUMENTS: DemoDocument[] = [
  {
    id: "d1",
    name: "Complete Blood Panel & INR",
    provider: "City Medical Lab",
    date: "Jan 12, 2025",
    type: "Lab Report",
    pages: 3,
  },
  {
    id: "d2",
    name: "Cardiology Prescription",
    provider: "Dr. Silva · Cardiology",
    date: "Mar 03, 2025",
    type: "Prescription",
    pages: 1,
  },
  {
    id: "d3",
    name: "Post-op Discharge Summary",
    provider: "General Hospital",
    date: "Jun 18, 2025",
    type: "Discharge Summary",
    pages: 4,
  },
  {
    id: "d4",
    name: "Family Medicine Prescription",
    provider: "Dr. Perera · Family Medicine",
    date: "Aug 07, 2025",
    type: "Prescription",
    pages: 1,
  },
  {
    id: "d5",
    name: "Endocrinology Follow-up Notes",
    provider: "Endocrinology Clinic",
    date: "Sep 22, 2025",
    type: "Doctor's Notes",
    pages: 2,
  },
];

export interface DemoLab {
  name: string;
  value: string;
  /** False marks a value outside its reference range — drawn in the danger colour. */
  inRange: boolean;
}

export interface DemoVisit {
  id: string;
  date: string;
  type: string;
  provider: string;
  /** True when a cross-check found something in this visit; colours the rail dot. */
  flagged: boolean;
  note: string;
  medications: string[];
  labs: DemoLab[];
}

export const DEMO_TIMELINE: DemoVisit[] = [
  {
    id: "v1",
    date: "Jan 12, 2025",
    type: "Lab Report",
    provider: "City Medical Lab",
    flagged: false,
    note: "Baseline panel. Penicillin allergy recorded in the intake history.",
    medications: [],
    labs: [
      { name: "INR", value: "2.1", inRange: true },
      { name: "HbA1c", value: "6.8%", inRange: false },
      { name: "Creatinine", value: "0.9", inRange: true },
    ],
  },
  {
    id: "v2",
    date: "Mar 03, 2025",
    type: "Prescription",
    provider: "Dr. Silva · Cardiology",
    flagged: false,
    note: "Anticoagulation started following an atrial-fibrillation diagnosis.",
    medications: ["Warfarin 5 mg — once daily", "Atorvastatin 20 mg — nightly"],
    labs: [],
  },
  {
    id: "v3",
    date: "Jun 18, 2025",
    type: "Discharge Summary",
    provider: "General Hospital",
    flagged: true,
    note: "Discharged after appendectomy. NSAID added for pain; diabetes regimen restated.",
    medications: ["Ibuprofen 400 mg — three times daily", "Metformin 1000 mg — once daily"],
    labs: [{ name: "INR", value: "3.6", inRange: false }],
  },
  {
    id: "v4",
    date: "Aug 07, 2025",
    type: "Prescription",
    provider: "Dr. Perera · Family Medicine",
    flagged: true,
    note: "Seen for a sinus infection; diabetes and cholesterol medications refilled.",
    medications: [
      "Amoxicillin 500 mg — 7 days",
      "Simvastatin 40 mg — nightly",
      "Metformin 500 mg — twice daily",
    ],
    labs: [],
  },
  {
    id: "v5",
    date: "Sep 22, 2025",
    type: "Doctor's Notes",
    provider: "Endocrinology Clinic",
    flagged: true,
    note: "INR flagged above range; endocrine plan revised for worsening control.",
    medications: [],
    labs: [
      { name: "HbA1c", value: "8.3%", inRange: false },
      { name: "INR", value: "4.2", inRange: false },
    ],
  },
];

/** Flags found across the timeline. Drives the sidebar badge until
 *  `GET /findings` replaces it. */
export const DEMO_FLAG_COUNT = 5;

/** Counted rather than written down, so editing a visit above cannot leave the
 *  header claiming something the list does not show. */
export const DEMO_COUNTS = {
  visits: DEMO_TIMELINE.length,
  medications: DEMO_TIMELINE.reduce((total, visit) => total + visit.medications.length, 0),
  labs: DEMO_TIMELINE.reduce((total, visit) => total + visit.labs.length, 0),
  providers: new Set(DEMO_TIMELINE.map((visit) => visit.provider)).size,
};
