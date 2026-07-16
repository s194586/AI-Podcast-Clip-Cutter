/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        app: {
          bg: 'rgb(var(--color-bg) / <alpha-value>)',
          panel: 'rgb(var(--color-panel) / <alpha-value>)',
          panelAlt: 'rgb(var(--color-panel-alt) / <alpha-value>)',
          border: 'rgb(var(--color-border) / <alpha-value>)',
          text: 'rgb(var(--color-text) / <alpha-value>)',
          muted: 'rgb(var(--color-muted) / <alpha-value>)',
          faint: 'rgb(var(--color-faint) / <alpha-value>)',
          accent: 'rgb(var(--color-accent) / <alpha-value>)',
          accentSoft: 'rgb(var(--color-accent-soft) / <alpha-value>)',
          danger: 'rgb(var(--color-danger) / <alpha-value>)',
          warning: 'rgb(var(--color-warning) / <alpha-value>)',
        },
      },
      boxShadow: {
        panel: '0 18px 45px rgb(0 0 0 / 0.28)',
      },
      borderRadius: {
        panel: '8px',
      },
    },
  },
  plugins: [],
}
