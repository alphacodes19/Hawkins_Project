"use client";

import { useEffect } from "react";

/**
 * Escape-to-close for modal dialogs. Small but was genuinely missing from
 * ConfirmDialog, UploadDialog, and FileViewerModal — all three only closed
 * via a mouse click before this, which is a real accessibility gap for
 * anyone navigating by keyboard.
 */
export function useEscapeKey(onEscape: () => void, enabled = true) {
  useEffect(() => {
    if (!enabled) return;
    function handler(e: KeyboardEvent) {
      if (e.key === "Escape") onEscape();
    }
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [onEscape, enabled]);
}
