const DRAFT_PREFIX = "ks:draft:";

export function draftKeys(): string[] {
  try {
    return Object.keys(window.localStorage).filter((k) => k.startsWith(DRAFT_PREFIX));
  } catch {
    return [];
  }
}

/** Code drafts stay in the browser, so a shared computer must lose them at sign-out (audit F-13). */
export function clearDrafts(): void {
  try {
    for (const key of draftKeys()) window.localStorage.removeItem(key);
  } catch {
    // Storage unavailable: nothing was stored either.
  }
}
