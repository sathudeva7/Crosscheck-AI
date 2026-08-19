/**
 * The little bit of markdown the model actually emits.
 *
 * Answers come back with `**Tel 5**` and the occasional `*maybe*` or
 * `` `code` `` in them. Printed verbatim those asterisks are noise, and a
 * reader looking at a medicine name has to mentally strip them off.
 *
 * Deliberately inline-only and deliberately not a markdown library. Nothing
 * here builds headings, lists or links, because nothing here should be able to
 * turn a sentence about someone's prescription into markup that changes what
 * the sentence appears to say. Text that is not one of the three spans below
 * is emitted unchanged, including stray asterisks — an unmatched `*` stays a
 * `*` rather than swallowing the rest of the paragraph.
 *
 * Only for prose the model wrote. Quoted document text — a laboratory's own
 * interpretation, a source span on the review screen — is shown exactly as it
 * was read, asterisks and all, and must not come through here.
 */

import { Fragment, type ReactNode } from "react";

/**
 * Bold first, so `**x**` is not read as an empty italic followed by `x`.
 * Each span must start and end on a non-space character, which is what keeps
 * arithmetic like `3 * 4 * 5` from turning into an emphasis run.
 */
const INLINE = /\*\*(\S(?:[^*]*\S)?)\*\*|\*(\S(?:[^*\n]*\S)?)\*|`([^`\n]+)`/g;

/**
 * `text` with its inline markdown rendered.
 *
 * Returns an array of nodes rather than an element so callers keep whatever
 * container — and whatever `white-space: pre-wrap` — they already had. Line
 * breaks are left to the caller's CSS for that reason.
 */
export function renderInline(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;

  // A fresh lastIndex each call: the regex is module-level and /g is stateful.
  INLINE.lastIndex = 0;

  for (let match = INLINE.exec(text); match; match = INLINE.exec(text)) {
    if (match.index > last) out.push(text.slice(last, match.index));

    const [whole, bold, italic, code] = match;
    const key = `${match.index}`;

    if (bold !== undefined) {
      out.push(<strong key={key}>{bold}</strong>);
    } else if (italic !== undefined) {
      out.push(<em key={key}>{italic}</em>);
    } else if (code !== undefined) {
      out.push(
        <code key={key} style={{ fontSize: "0.92em" }}>
          {code}
        </code>,
      );
    }

    last = match.index + whole.length;
  }

  if (last < text.length) out.push(text.slice(last));
  return out;
}

/** `renderInline` as an element, for the common case of rendering one string. */
export function Markdown({ text }: { text: string }) {
  return <Fragment>{renderInline(text)}</Fragment>;
}
