/** Paygent design tokens.
 *
 * Warm paper, ink, and three semantic colours. No greys from a default palette —
 * every neutral here is warm-tinted so the whole surface reads as paper rather
 * than as a screen.
 */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        paper:    { DEFAULT: '#FBF8F1', deep: '#F5F1E8', raised: '#FFFDF7' },
        ink:      { DEFAULT: '#17140E', 2: '#4A4437', 3: '#8A8272', 4: '#B5AC98' },
        rule:     { DEFAULT: '#E2DCCC', strong: '#CFC7B2' },
        // Loss, ledger disagreement, guardrail block.
        oxblood:  { DEFAULT: '#8C2B22', bg: '#F6EAE7', edge: '#DFC0BA' },
        // Recovered, healthy, positive.
        forest:   { DEFAULT: '#2B5540', bg: '#E9F0EA', edge: '#BDD1C2' },
        // Modelled / estimated figures. Never used for a confirmed number.
        ochre:    { DEFAULT: '#96681C', bg: '#F5EEDF', edge: '#DFCCA4' },
        navy:     { DEFAULT: '#1E3A5C', bg: '#E8EDF3' },
      },
      fontFamily: {
        display: ['Newsreader', 'Iowan Old Style', 'Georgia', 'serif'],
        sans:    ['IBM Plex Sans', 'system-ui', 'sans-serif'],
        mono:    ['IBM Plex Mono', 'ui-monospace', 'monospace'],
      },
      fontSize: {
        label: ['0.6875rem', { lineHeight: '1', letterSpacing: '0.14em' }],
        micro: ['0.75rem', { lineHeight: '1.35' }],
        base:  ['0.9375rem', { lineHeight: '1.55' }],
        stat:  ['2.75rem', { lineHeight: '1', letterSpacing: '-0.02em' }],
        hero:  ['4rem', { lineHeight: '0.95', letterSpacing: '-0.03em' }],
      },
      borderRadius: { DEFAULT: '2px', sm: '1px' },
      maxWidth: { canvas: '1360px' },
    },
  },
  plugins: [],
}
