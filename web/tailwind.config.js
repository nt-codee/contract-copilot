/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        desk: { DEFAULT: "#1E3A6E", deep: "#152B54", line: "#2E4E8A", text: "#C4D2EE" },
        paper: { DEFAULT: "#FFFFFF", soft: "#F3F5F9", line: "#DCE2EC" },
        ink: { DEFAULT: "#121A2B", soft: "#44506A", mute: "#5B6680" },
        high: { DEFAULT: "#B3261E", box: "#D93A2B", tint: "#FCE3DF" },
        medium: { DEFAULT: "#8A5300", box: "#E9A400", tint: "#FFF0CC" },
        low: { DEFAULT: "#1B6B4A", box: "#2F9E6E", tint: "#DDF2E7" },
        mark: "#FFC94A",
      },
      fontFamily: {
        sans: ['"Archivo Variable"', "system-ui", "sans-serif"],
        doc: ["Tinos", '"Times New Roman"', "Times", "serif"],
      },
      boxShadow: {
        // Layered: contact shadow, near shadow, long soft ambient. Pages sit on the desk, not on a flat fill.
        page: "0 1px 1px rgba(5,15,40,.30), 0 4px 10px -4px rgba(5,15,40,.35), 0 18px 40px -18px rgba(5,15,40,.60)",
        lift: "0 2px 6px rgba(5,15,40,.14), 0 18px 40px -16px rgba(5,15,40,.40)",
        // The one surface that floats above a page (hero finding card).
        float: "0 1px 2px rgba(5,15,40,.25), 0 10px 24px -8px rgba(5,15,40,.45), 0 36px 64px -28px rgba(5,15,40,.70)",
      },
      transitionTimingFunction: {
        settle: "cubic-bezier(0.3, 0.7, 0.2, 1)",
      },
    },
  },
  plugins: [],
};
