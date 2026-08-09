import Image from "next/image";

/**
 * A single shared watermark, used identically on the login page and the
 * main app shell. Previously each page positioned its own copy differently
 * (login: rotated, pushed off-canvas bottom-right; app: unrotated, pinned to
 * the exact corner) — inconsistent placement between pages is what read as
 * "not aligned to the page." This version is centered in its container on
 * both, with no rotation, so it reads as one deliberate mark rather than
 * two different decorative choices that happen to use the same image.
 */
export function LogoWatermark({ fixed = false, className = "" }: { fixed?: boolean; className?: string }) {
  return (
    <div
      className={`pointer-events-none select-none ${fixed ? "fixed" : "absolute"} inset-0 flex items-center justify-center overflow-hidden ${className}`}
      aria-hidden
    >
      <Image
        src="/hawkins-logo-full.png"
        alt=""
        width={620}
        height={335}
        className="opacity-[0.02] w-[42vw] max-w-[620px] h-auto"
      />
    </div>
  );
}
