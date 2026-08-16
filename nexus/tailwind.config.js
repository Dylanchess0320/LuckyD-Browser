/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        bg: {
          DEFAULT: 'rgb(var(--bg) / <alpha-value>)',
          panel: 'rgb(var(--panel) / <alpha-value>)',
          hover: 'rgb(var(--hover) / <alpha-value>)',
          border: 'rgb(var(--border) / <alpha-value>)',
        },
        accent: {
          DEFAULT: 'rgb(var(--accent) / <alpha-value>)',
          hover: 'rgb(var(--accent-hover) / <alpha-value>)',
          muted: 'rgb(var(--accent) / 0.16)',
        },
        content: {
          DEFAULT: 'rgb(var(--text) / <alpha-value>)',
          dim: 'rgb(var(--dim) / <alpha-value>)',
          faint: 'rgb(var(--faint) / <alpha-value>)',
        },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'sans-serif'],
        mono: ['JetBrains Mono', 'Consolas', 'monospace'],
      },
      maxWidth: { chat: '48rem' },
    },
  },
  plugins: [],
}
