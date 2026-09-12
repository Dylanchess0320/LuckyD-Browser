/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx,css}'],
  darkMode: 'class',
  theme: {
    extend: {
      // LuckyD Neon Night tokens — mirrors browser/browser_core/brand.py PALETTES["neon"]
      colors: {
        ld: {
          window: '#0b0f1a',
          panel: '#10151f',
          panel2: '#141a28',
          card: '#1a2132',
          border: '#232c42',
          text: '#e8ecf5',
          muted: '#8b93a7',
          accent: '#5b9dff',
          accent2: '#b46bff',
          ok: '#34d399',
          danger: '#ff5b6e',
          warn: '#fbbf24',
          // faint/disabled derived from muted — mirrors brand._rgba()
          faint: 'rgba(139,147,167,0.6)',
        },
      },
      fontFamily: {
        sans: ['"Segoe UI Variable"', '"Segoe UI"', 'system-ui', 'sans-serif'],
        mono: ['"Cascadia Code"', '"JetBrains Mono"', 'Consolas', 'monospace'],
      },
      boxShadow: {
        'ld-1': '0 1px 2px rgba(0,0,0,.28)',
        'ld-2': '0 4px 16px rgba(0,0,0,.35)',
        'ld-3': '0 12px 40px rgba(0,0,0,.5)',
      },
    },
  },
  plugins: [],
}
