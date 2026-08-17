/**
 * One Health Icons glyph, inlined so it takes the colour around it.
 *
 * The files are imported with Vite's `?raw` suffix rather than as image URLs,
 * which matters for one reason: every Health Icon is drawn with
 * `fill="currentColor"`, and that only resolves when the SVG is part of the
 * document. Loaded through `<img src>` it would render black and could not be
 * tinted to the accent without a CSS filter hack.
 *
 * Inlining also means no network request per icon and no flash of empty space
 * while they load — they are bundled into the page like any other module.
 *
 * See `../assets/healthicons/LICENCE.md`. CC0, no attribution required.
 */

import allergy from "../assets/healthicons/allergy.svg?raw";
import critical from "../assets/healthicons/critical.svg?raw";
import crosscheck from "../assets/healthicons/crosscheck.svg?raw";
import dosage from "../assets/healthicons/dosage.svg?raw";
import duplicate from "../assets/healthicons/duplicate.svg?raw";
import interactions from "../assets/healthicons/interactions.svg?raw";
import referral from "../assets/healthicons/referral.svg?raw";
import sources from "../assets/healthicons/sources.svg?raw";
import trends from "../assets/healthicons/trends.svg?raw";
import upload from "../assets/healthicons/upload.svg?raw";
import verify from "../assets/healthicons/verify.svg?raw";

const ICONS = {
  allergy,
  critical,
  crosscheck,
  dosage,
  duplicate,
  interactions,
  referral,
  sources,
  trends,
  upload,
  verify,
} as const;

export type HealthIconName = keyof typeof ICONS;

/**
 * The upstream files are a fixed 48px square. Stripping the width and height
 * lets the viewBox do the scaling, so one file serves a 20px bullet and a 40px
 * panel mark without shipping two copies.
 */
function sized(svg: string, size: number): string {
  return svg.replace(
    /<svg[^>]*?>/,
    (tag) =>
      tag
        .replace(/\swidth="[^"]*"/, "")
        .replace(/\sheight="[^"]*"/, "")
        .replace("<svg", `<svg width="${size}" height="${size}"`),
  );
}

export default function HealthIcon({
  name,
  size = 22,
  color = "var(--color-accent)",
  title,
}: {
  name: HealthIconName;
  size?: number;
  color?: string;
  /** Given, the icon is announced. Omitted, it is decoration and is hidden. */
  title?: string;
}) {
  return (
    <span
      role={title ? "img" : undefined}
      aria-label={title}
      aria-hidden={title ? undefined : true}
      style={{ display: "inline-flex", color, flex: "none", lineHeight: 0 }}
      dangerouslySetInnerHTML={{ __html: sized(ICONS[name], size) }}
    />
  );
}
