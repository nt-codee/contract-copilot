/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      // Seven steps, no others. Floor 13px (meta only); body 16px. Bringhurst's classic scale
      // (… 12 14 16 18 24 36 48 …), with 13/15 for meta/UI text and 56 for the one hero line.
      fontSize: {
        xs: [".8125rem", { lineHeight: "1.25rem" }], // 13  meta: dates, folios, counts, captions
        sm: [".9375rem", { lineHeight: "1.4rem" }], // 15  secondary text, labels, buttons in dense UI
        base: ["1rem", { lineHeight: "1.5rem" }], // 16  body
        lg: ["1.125rem", { lineHeight: "1.65rem" }], // 18  lede, verdict sentence, card titles
        xl: ["1.5rem", { lineHeight: "1.2" }], // 24  section titles, entry titles
        "2xl": ["2.25rem", { lineHeight: "1.08" }], // 36  page titles
        "3xl": ["3.5rem", { lineHeight: "1.04" }], // 56  home hero only
      },
      colors: {
        // Primary action and navigation. Signature blue: the ink you sign in, not a risk colour.
        // #2450C0 on white 7.0:1 (white text on it 7.0:1); bright #9DB9F5 on desk 5.7:1, on desk-deep 7.1:1.
        accent: { DEFAULT: "#2450C0", deep: "#1C409B", tint: "#E3EBFB", bright: "#9DB9F5" },
        desk: { DEFAULT: "#1E3A6E", deep: "#152B54", line: "#2E4E8A", text: "#C4D2EE" },
        paper: { DEFAULT: "#FFFFFF", panel: "#F5F7FA", soft: "#EDF1F6", line: "#DCE2EC" },
        ink: { DEFAULT: "#121A2B", soft: "#44506A", mute: "#5B6680" },
        high: { DEFAULT: "#B3261E", box: "#D93A2B", tint: "#FCE3DF" },
        medium: { DEFAULT: "#8A5300", box: "#E9A400", tint: "#FFF0CC" },
        low: { DEFAULT: "#1B6B4A", box: "#2F9E6E", tint: "#DDF2E7" },
        mark: "#FFC94A",
      },
      fontFamily: {
        sans: ['"Public Sans Variable"', "system-ui", "sans-serif"],
        doc: ["Tinos", '"Times New Roman"', "Times", "serif"],
      },
      boxShadow: {
        // Layered: contact shadow, near shadow, long soft ambient. Pages sit on the desk, not on a flat fill.
        page: "0 1px 1px rgba(5,15,40,.30), 0 4px 10px -4px rgba(5,15,40,.35), 0 18px 40px -18px rgba(5,15,40,.60)",
        lift: "0 2px 6px rgba(5,15,40,.14), 0 18px 40px -16px rgba(5,15,40,.40)",
        // The one surface that floats above a page (hero finding card).
        float: "0 1px 2px rgba(5,15,40,.25), 0 10px 24px -8px rgba(5,15,40,.45), 0 36px 64px -28px rgba(5,15,40,.70)",
      },
      // Spacing rhythm for the review rail (and any stacked panel): one gap between sections,
      // one inside a section. Dividers go between sections only.
      spacing: {
        section: "1.75rem", // 28px: padding above/below a section, and between sections
        inner: "0.75rem", // 12px: between blocks inside a section
      },
      transitionTimingFunction: {
        settle: "cubic-bezier(0.3, 0.7, 0.2, 1)",
      },
    },
  },
  plugins: [],
};
