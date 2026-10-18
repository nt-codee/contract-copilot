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
        page: "0 1px 2px rgba(5,15,40,.35), 0 12px 32px -12px rgba(5,15,40,.55)",
        lift: "0 2px 6px rgba(5,15,40,.12), 0 18px 40px -16px rgba(5,15,40,.35)",
      },
    },
  },
  plugins: [],
};
