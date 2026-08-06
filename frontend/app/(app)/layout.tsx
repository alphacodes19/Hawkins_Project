"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/context/auth-context";
import { Sidebar } from "@/components/Sidebar";
import { LogoWatermark } from "@/components/LogoWatermark";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (!loading && !user) router.replace("/login");
  }, [loading, user, router]);

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-canvas">
        <div className="w-2 h-2 rounded-full bg-ink-faint animate-pulseDot" />
      </div>
    );
  }

  if (!user) return null; // redirect effect above is about to fire

  return (
    <div className="flex">
      <Sidebar />
      <main className="flex-1 min-w-0 relative">
        {/* Fixed to the viewport (not the scrolling content), centered
            consistently with the login page's watermark via the shared
            LogoWatermark component — see that file for why "consistent"
            matters here (previously login and app used different
            rotation/positioning, which read as misaligned). */}
        <LogoWatermark fixed className="z-0" />
        <div className="relative z-10">{children}</div>
      </main>
    </div>
  );
}
