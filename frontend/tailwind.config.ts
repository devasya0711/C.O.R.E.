import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: "class",
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        canvas: "#090A0F",
        panel: "#11131C",
        surface: "#181B26",
        hairline: "#262938",
        active: "#4F46E5",
        primaryText: "#F3F4F6",
        secondaryText: "#9CA3AF",
        mutedText: "#6B7280",
      },
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "SFMono-Regular", "monospace"],
      },
    },
  },
  plugins: [],
};

export default config;
