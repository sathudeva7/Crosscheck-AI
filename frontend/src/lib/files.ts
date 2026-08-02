/**
 * What the uploader will accept, and why.
 *
 * These are the backend's rules, not invented ones — `ALLOWED_MIME_TYPES` and
 * `MAX_UPLOAD_MB` in app/config.py. Checking here is a courtesy so a mistake is
 * caught before a 25 MB file crosses the network; the server still enforces
 * them, because a browser check is a hint, never a guarantee.
 */

export const ACCEPTED_TYPES = ["application/pdf", "image/jpeg", "image/png"] as const;

/** For the file input's `accept` attribute: extensions get better OS dialogs
 *  than MIME types alone, so give both. */
export const ACCEPT_ATTRIBUTE = ".pdf,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png";

export const MAX_UPLOAD_MB = 25;
const MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024;

export interface PickedFile {
  id: string;
  file: File;
  /** Null when the file is fine; otherwise the sentence shown against it. */
  problem: string | null;
}

/** A size a person can read: 940 KB, 2.4 MB. */
export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const kb = bytes / 1024;
  if (kb < 1024) return `${Math.round(kb)} KB`;
  return `${(kb / 1024).toFixed(1)} MB`;
}

function checkFile(file: File): string | null {
  // Some browsers report an empty type for files dragged from certain apps, so
  // fall back to the extension rather than rejecting something valid.
  const byType = (ACCEPTED_TYPES as readonly string[]).includes(file.type);
  const byExtension = /\.(pdf|jpe?g|png)$/i.test(file.name);

  if (!byType && !byExtension) return "Not a PDF, JPG or PNG.";
  if (file.size > MAX_UPLOAD_BYTES) {
    return `${formatSize(file.size)} — over the ${MAX_UPLOAD_MB} MB limit.`;
  }
  if (file.size === 0) return "The file is empty.";
  return null;
}

/**
 * Turns a FileList into rows, skipping ones already picked.
 *
 * Same name and size counts as the same file: browsers give no stable id, and
 * this is the pair that catches the real case — dropping a folder twice.
 */
export function acceptFiles(incoming: FileList | File[], existing: PickedFile[]): PickedFile[] {
  const seen = new Set(existing.map((row) => `${row.file.name}:${row.file.size}`));
  const added: PickedFile[] = [];

  for (const file of Array.from(incoming)) {
    const key = `${file.name}:${file.size}`;
    if (seen.has(key)) continue;
    seen.add(key);
    added.push({ id: crypto.randomUUID(), file, problem: checkFile(file) });
  }

  return added;
}
