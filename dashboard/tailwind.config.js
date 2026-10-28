/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        surface: "#ffffff",
        canvas: "#fafafa",
        border: {
          DEFAULT: "#e5e5e5",
          strong: "#d4d4d4",
        },
        ink: {
          DEFAULT: "#171717",
          muted: "#525252",
          faint: "#a3a3a3",
        },
        accent: {
          DEFAULT: "#2563eb",
          hover: "#1d4ed8",
          soft: "#eff6ff",
        },
        success: { DEFAULT: "#16a34a", soft: "#f0fdf4" },
        warning: { DEFAULT: "#d97706", soft: "#fffbeb" },
        danger: { DEFAULT: "#dc2626", soft: "#fef2f2" },
      },
      fontFamily: {
        sans: [
          "Inter",
          "-apple-system",
          "BlinkMacSystemFont",
          "Segoe UI",
          "sans-serif",
        ],
      },
      boxShadow: {
        card: "0 1px 2px 0 rgb(0 0 0 / 0.04)",
        popover: "0 4px 16px -2px rgb(0 0 0 / 0.10), 0 2px 4px -2px rgb(0 0 0 / 0.06)",
      },
    },
  },
  plugins: [],
};
