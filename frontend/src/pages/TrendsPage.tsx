/**
 * Every measurement, plotted across every report that contains it.
 *
 * The chart is hand-drawn SVG rather than a charting library. Sixty lines
 * against a hundred kilobytes, and it keeps the square corners and hairlines
 * the rest of the app is built from — a rounded, tooltipped, animated chart
 * dropped in here would read as borrowed.
 *
 * Three things this screen refuses to do, all for the same reason:
 *
 *   No verdict without a reference range. Most documents record none, and
 *   'high' asserted without one is diagnosis rather than reading. Those series
 *   show the line and the direction and stop there.
 *
 *   No line through a word. 'Positive for S. aureus' is a real result and is
 *   listed, but it has no position on an axis.
 *
 *   No trend from one reading. A single point renders as a value, not a line
 *   with a direction attached to it.
 *
 * What it does do, deliberately, is make a misread number obvious. A height of
 * 200cm after 130cm is invisible in a table and unmissable on a chart, so any
 * reading still awaiting review is marked and linked to the review screen.
 */

import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Corners } from "../components/chrome";
import { describeError } from "../lib/errors";
import { STATUS, TREND, listSeries, num, shortDate, shown } from "../lib/labs";
import type { Series } from "../lib/labs";
import { useAuth } from "../lib/useAuth";

const MUTED = "rgba(29,31,32,.6)";
const FAINT = "rgba(29,31,32,.45)";

const CHART = { width: 640, height: 132, padX: 8, padY: 14 };

/**
 * One scale for the whole chart, so the line and the normal band agree.
 *
 * The reference range is folded into the extent rather than clipped to it: a
 * value far outside its range must stay on the canvas, because that reading is
 * the entire reason to look.
 */
function geometry(values: number[], refLow: number | null, refHigh: number | null, count: number) {
  const all = [...values, ...(refLow !== null ? [refLow] : []), ...(refHigh !== null ? [refHigh] : [])];
  const lo = Math.min(...all);
  const hi = Math.max(...all);
  // A flat series has no span to divide by. Give it one, or every point
  // collapses onto a single edge.
  const span = hi - lo || Math.abs(hi) || 1;
  const top = hi + span * 0.12;
  const bottom = lo - span * 0.12;

  const usableW = CHART.width - CHART.padX * 2;
  const usableH = CHART.height - CHART.padY * 2;

  return {
    // Spaced by order rather than by elapsed time. Readings arrive whenever a
    // document happened to be issued, and a true time axis leaves four years
    // of empty space with every point crushed against one end.
    x: (index: number) =>
      CHART.padX + (count === 1 ? usableW / 2 : (usableW * index) / (count - 1)),
    y: (value: number) => CHART.padY + usableH * (1 - (value - bottom) / (top - bottom)),
  };
}

function Chart({ series }: { series: Series }) {
  const plottable = series.readings.filter((r) => r.plottable);
  const { x, y } = geometry(
    plottable.map((r) => r.value as number),
    series.ref_low,
    series.ref_high,
    plottable.length,
  );

  const plotted = plottable.map((reading, index) => ({
    x: x(index),
    y: y(reading.value as number),
    reading,
  }));
  const path = plotted
    .map((p, i) => `${i === 0 ? "M" : "L"}${p.x.toFixed(1)} ${p.y.toFixed(1)}`)
    .join(" ");

  const bandTop = series.ref_high !== null ? y(series.ref_high) : null;
  const bandBottom = series.ref_low !== null ? y(series.ref_low) : null;

  return (
    <svg
      viewBox={`0 0 ${CHART.width} ${CHART.height}`}
      width="100%"
      height={CHART.height}
      role="img"
      aria-label={`${series.name}, ${plotted.length} readings, ${
        series.direction ? TREND[series.direction].toLowerCase() : "no trend"
      }`}
      style={{ display: "block", overflow: "visible" }}
    >
      {bandTop !== null && bandBottom !== null && (
        <rect
          x={0}
          y={Math.min(bandTop, bandBottom)}
          width={CHART.width}
          height={Math.abs(bandBottom - bandTop)}
          fill="rgba(60,95,72,.10)"
        />
      )}

      <path d={path} fill="none" stroke="var(--color-accent)" strokeWidth={1.6} />

      {plotted.map(({ x, y, reading }) => {
        const outside = reading.status === "high" || reading.status === "low";
        return (
          <g key={reading.event_id}>
            <rect
              x={x - 3}
              y={y - 3}
              width={6}
              height={6}
              fill={outside ? "var(--color-danger)" : "var(--color-accent)"}
              stroke={reading.needs_review ? "var(--color-warning)" : "none"}
              strokeWidth={reading.needs_review ? 2 : 0}
            />
            <title>
              {shown(reading)} · {shortDate(reading.date)}
            </title>
          </g>
        );
      })}
    </svg>
  );
}

/** The readings under the chart, so exact values stay readable. */
function Values({ series }: { series: Series }) {
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
      {series.readings.map((reading) => {
        const tone = reading.status ? STATUS[reading.status] : null;
        return (
          <span
            key={reading.event_id}
            style={{
              display: "inline-flex",
              flexDirection: "column",
              padding: "5px 10px",
              border: "1px solid var(--color-divider)",
              background: tone && reading.status !== "normal" ? tone.bg : "var(--color-surface)",
              color: tone && reading.status !== "normal" ? tone.text : undefined,
            }}
          >
            <span style={{ fontSize: 13, fontVariantNumeric: "tabular-nums" }}>
              {shown(reading)}
              {reading.needs_review && (
                <span style={{ color: "var(--color-warning-text)" }} title="Not yet confirmed">
                  {" "}
                  ·&nbsp;unconfirmed
                </span>
              )}
            </span>
            <span style={{ fontSize: 11, color: FAINT }}>{shortDate(reading.date)}</span>
          </span>
        );
      })}
    </div>
  );
}

function Card({ series }: { series: Series }) {
  const latest = series.latest;
  const tone = latest?.status ? STATUS[latest.status] : null;
  const unreviewed = series.readings.filter((r) => r.needs_review).length;

  return (
    <div className="card blueprint" style={{ padding: "16px 18px", gap: 12 }}>
      <Corners />

      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          justifyContent: "space-between",
          gap: 12,
          flexWrap: "wrap",
        }}
      >
        <div>
          <h3 style={{ margin: 0, fontSize: 19 }}>{series.name}</h3>
          <span style={{ fontSize: 12, color: FAINT }}>
            {series.readings.length} reading{series.readings.length === 1 ? "" : "s"}
            {/* A merge the reader cannot see is a merge they cannot correct. */}
            {series.source_names.length > 1 && ` · recorded as ${series.source_names.join(", ")}`}
          </span>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          {latest && (
            <span style={{ fontSize: 17, fontVariantNumeric: "tabular-nums" }}>
              {shown(latest)}
            </span>
          )}
          {tone && (
            <span
              style={{
                fontSize: 11.5,
                padding: "3px 9px",
                background: tone.bg,
                color: tone.text,
              }}
            >
              {tone.label}
            </span>
          )}
          {series.direction && (
            <span style={{ fontSize: 11.5, padding: "3px 9px", background: "var(--color-neutral-100)" }}>
              {TREND[series.direction]}
              {series.change !== null && series.direction !== "steady" && (
                <> · {series.change > 0 ? "+" : ""}{num(series.change)}{series.unit ? ` ${series.unit}` : ""}</>
              )}
            </span>
          )}
        </div>
      </div>

      {series.chartable && <Chart series={series} />}

      <Values series={series} />

      {/* Each of these is an absence the reader is entitled to have named,
          rather than a blank space they have to interpret. */}
      {!series.chartable && series.readings.every((r) => r.value === null) && (
        <p style={{ margin: 0, fontSize: 12.5, color: MUTED }}>
          These results are descriptions rather than numbers, so there is nothing to plot.
        </p>
      )}

      {!series.chartable && series.readings.some((r) => r.value !== null) && (
        <p style={{ margin: 0, fontSize: 12.5, color: MUTED }}>
          Only one comparable reading so far — a direction needs at least two.
        </p>
      )}

      {series.ref_low === null && series.ref_high === null && series.chartable && (
        <p style={{ margin: 0, fontSize: 12.5, color: MUTED }}>
          No normal range was recorded for this test, so CrossCheck can show the movement but not
          say whether a value is high or low.
        </p>
      )}

      {unreviewed > 0 && (
        <p style={{ margin: 0, fontSize: 12.5 }}>
          <span style={{ color: "var(--color-warning-text)" }}>
            {unreviewed} reading{unreviewed === 1 ? "" : "s"} not yet confirmed.
          </span>{" "}
          <Link to="/app/review" style={{ color: "var(--color-accent)" }}>
            Check them
          </Link>
        </p>
      )}
    </div>
  );
}

export default function TrendsPage() {
  const { getToken } = useAuth();
  const [series, setSeries] = useState<Series[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setSeries(await listSeries(await getToken()));
      setError(null);
    } catch (problem) {
      setError(describeError(problem));
    }
  }, [getToken]);

  useEffect(() => {
    void load();
  }, [load]);

  const charted = series?.filter((s) => s.chartable).length ?? 0;

  return (
    <div className="cc-rise">
      <h6 style={{ color: "var(--color-accent)", marginBottom: 4 }}>Results over time</h6>
      <h2 style={{ margin: "0 0 6px", fontSize: 30 }}>Lab trends</h2>
      <p style={{ color: MUTED, maxWidth: "64ch", marginBottom: 20 }}>
        Every measurement across every report that contains it. Readings recorded under different
        names are joined into one history, and a value is only called high or low where the
        document itself supplied a normal range.
      </p>

      {error && (
        <div className="card" style={{ padding: "14px 16px", gap: 8 }}>
          <p style={{ margin: 0, fontSize: 14, color: "var(--color-danger-text)" }}>{error}</p>
          <button type="button" className="btn btn-secondary" onClick={() => void load()}>
            Try again
          </button>
        </div>
      )}

      {/* `null` is 'not asked yet', not 'nothing found'. */}
      {!error && series === null && (
        <p style={{ color: FAINT, fontSize: 14 }}>Reading your measurements…</p>
      )}

      {!error && series !== null && series.length === 0 && (
        <div className="card blueprint" style={{ padding: "20px 22px", gap: 8 }}>
          <Corners />
          <h3 style={{ margin: 0, fontSize: 18 }}>No measurements yet</h3>
          <p style={{ margin: 0, fontSize: 14, color: MUTED, maxWidth: "58ch" }}>
            Lab values and vital signs appear here once a report containing them has been uploaded
            and read.
          </p>
          <Link to="/app/documents" className="btn btn-secondary" style={{ alignSelf: "flex-start" }}>
            Add a document
          </Link>
        </div>
      )}

      {!error && series !== null && series.length > 0 && (
        <>
          <p style={{ fontSize: 12.5, color: FAINT, margin: "0 0 14px" }}>
            {series.length} measurement{series.length === 1 ? "" : "s"} on record ·{" "}
            {charted} with enough history to plot
          </p>
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            {series.map((item) => (
              <Card key={item.key} series={item} />
            ))}
          </div>
        </>
      )}
    </div>
  );
}
