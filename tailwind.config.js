/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        primary: "var(--color-primary)",
        "on-primary": "var(--color-on-primary)",
        secondary: "var(--color-secondary)",
        accent: "var(--color-accent)",
        "on-accent": "var(--color-on-accent)",
        background: "var(--color-background)",
        foreground: "var(--color-foreground)",
        card: "var(--color-card)",
        "card-foreground": "var(--color-card-foreground)",
        muted: "var(--color-muted)",
        "muted-foreground": "var(--color-muted-foreground)",
        border: "var(--color-border)",
        destructive: "var(--color-destructive)",
        warning: "var(--color-warning)",
        ring: "var(--color-ring)",
      },
      fontFamily: {
        sans: ['Plus Jakarta Sans', 'sans-serif'],
        mono: ['JetBrains Mono', 'IBM Plex Mono', 'monospace'],
      },
    },
  },
  plugins: [],
}
