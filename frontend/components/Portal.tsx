"use client";

import { useEffect, useState } from "react";
import { createPortal } from "react-dom";

/**
 * Renders children directly into document.body, bypassing wherever the
 * component is mounted in the React tree. This is what modals actually need:
 * a `position: fixed` overlay nested inside ANY ancestor with `overflow`,
 * `transform`, `filter`, or `will-change` set can get clipped or mispainted
 * by that ancestor even though `fixed` is supposed to position relative to
 * the viewport — a well-known CSS gotcha, not a browser bug. The sidebar's
 * `overflow-hidden` (needed to clip content cleanly during its collapse
 * animation) is exactly this kind of ancestor, and every modal in this app
 * (UploadDialog, ConfirmDialog, FileViewerModal) used to be a DOM descendant
 * of it. A portal sidesteps the entire category of bug instead of hoping no
 * ancestor ever grows one of these properties again.
 */
export function Portal({ children }: { children: React.ReactNode }) {
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  if (!mounted) return null;
  return createPortal(children, document.body);
}
