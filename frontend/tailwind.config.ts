import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Cool near-white surface, not the generic warm cream — reads calmer
        // and more "instrument panel" for a document-search tool.
        canvas: "#F5F6F8",
        surface: "#FFFFFF",
        border: "#E3E6EB",
        ink: {
          DEFAULT: "#16213D", // deep navy — extends the #1B2A4A already in the Hawkins UI
          muted: "#5B6478",
          faint: "#8A93A6",
        },
        accent: {
          DEFAULT: "#8B2635", // Hawkins cookware maroon/red, not AI-tool terracotta
          hover: "#731F2B",
          soft: "#FBEAEC",
        },
        success: { DEFAULT: "#2F7D5C", soft: "#E7F4EE" },
        warning: { DEFAULT: "#9A6B00", soft: "#FBF3DE" },
        danger: { DEFAULT: "#C0392B", soft: "#FBEAE8" },
      },
      fontFamily: {
        sans: ["var(--font-plex-sans)", "system-ui", "sans-serif"],
        mono: ["var(--font-plex-mono)", "ui-monospace", "monospace"],
      },
      boxShadow: {
        card: "0 1px 2px rgba(22, 33, 61, 0.04), 0 1px 1px rgba(22, 33, 61, 0.03)",
        popover: "0 8px 24px rgba(22, 33, 61, 0.12)",
      },
      borderRadius: {
        md: "8px",
        lg: "12px",
      },
      keyframes: {
        pulseDot: {
          "0%, 100%": { opacity: "0.3" },
          "50%": { opacity: "1" },
        },
        fadeIn: {
          from: { opacity: "0", transform: "translateY(-2px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
      },
      animation: {
        pulseDot: "pulseDot 1.1s ease-in-out infinite",
        fadeIn: "fadeIn 0.15s ease-out",
      },
    },
  },
  plugins: [],
};

export default config;
