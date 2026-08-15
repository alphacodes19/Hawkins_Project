"use client";

import { useEffect, useState } from "react";
import { useAuth } from "@/context/auth-context";

const VISIBLE_MS = 3200;
const TRANSITION_MS = 200;

export function WelcomeToast() {
  const { user, justLoggedIn, clearJustLoggedIn } = useAuth();
  const [mounted, setMounted] = useState(false);
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    if (!justLoggedIn) return;

    setMounted(true);
    // Mount at opacity-0/translated, then flip to visible on the next tick
    // so the CSS transition below actually animates in rather than
    // snapping straight to its end state.
    const enter = setTimeout(() => setVisible(true), 10);
    const exit = setTimeout(() => setVisible(false), VISIBLE_MS);
    const unmount = setTimeout(() => {
      setMounted(false);
      clearJustLoggedIn();
    }, VISIBLE_MS + TRANSITION_MS);

    return () => {
      clearTimeout(enter);
      clearTimeout(exit);
      clearTimeout(unmount);
    };
  }, [justLoggedIn, clearJustLoggedIn]);

  if (!mounted || !user) return null;

  return (
    <div
      role="status"
      aria-live="polite"
      className={`fixed top-5 right-5 z-50 transition-all duration-200 ease-out
                  ${visible ? "opacity-100 translate-y-0" : "opacity-0 -translate-y-2"}`}
    >
      <div className="flex items-center gap-2.5 bg-surface border border-border shadow-popover rounded-lg pl-3.5 pr-4 py-3">
        <span className="w-2 h-2 rounded-full bg-success shrink-0" />
        <p className="text-sm text-ink">
          <span className="font-medium">Welcome back, {user.username}!</span>
        </p>
      </div>
    </div>
  );
}
