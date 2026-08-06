"use client";

import { useEscapeKey } from "@/lib/use-escape-key";
import { Portal } from "./Portal";

/**
 * Reusable confirmation modal for destructive/creation admin actions.
 * Didn't exist in the original Streamlit forms (which submitted immediately)
 * — this is new, not a restoration.
 */
export function ConfirmDialog({
  title,
  message,
  confirmLabel = "Confirm",
  danger = false,
  onConfirm,
  onCancel,
}: {
  title: string;
  message: string;
  confirmLabel?: string;
  danger?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  useEscapeKey(onCancel);

  return (
    <Portal>
      <div
        className="fixed inset-0 bg-ink/40 z-50 flex items-center justify-center px-4"
        onClick={onCancel}
        role="dialog"
        aria-modal="true"
        aria-labelledby="confirm-dialog-title"
      >
        <div
          className="bg-surface rounded-lg shadow-popover w-full max-w-sm p-5 animate-fadeIn"
          onClick={(e) => e.stopPropagation()}
        >
          <h2 id="confirm-dialog-title" className="text-sm font-semibold text-ink mb-1.5">
            {title}
          </h2>
          <p className="text-sm text-ink-muted mb-5">{message}</p>
          <div className="flex justify-end gap-2">
            <button
              onClick={onCancel}
              autoFocus
              className="text-sm font-medium text-ink-muted hover:text-ink border border-border rounded-md px-3.5 py-1.5 transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={onConfirm}
              className={`text-sm font-medium text-white rounded-md px-3.5 py-1.5 transition-colors ${
                danger ? "bg-danger hover:bg-danger/90" : "bg-accent hover:bg-accent-hover"
              }`}
            >
              {confirmLabel}
            </button>
          </div>
        </div>
      </div>
    </Portal>
  );
}
