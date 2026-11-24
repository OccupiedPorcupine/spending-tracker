import { load, save } from './store';

export type Theme = 'system' | 'light' | 'dark';
const COLORS = { light: '#e9ecf1', dark: '#0e1117' };

export const currentTheme = (): Theme => load<Theme>('theme', 'system');

/** Apply and remember a theme on this device. public/theme.js does the same on page load. */
export function setTheme(t: Theme): void {
  save('theme', t);
  const root = document.documentElement;
  if (t === 'system') root.removeAttribute('data-theme'); else root.setAttribute('data-theme', t);
  // The status bar colour: forced themes use one colour; "system" goes back to one per scheme.
  document.querySelectorAll<HTMLMetaElement>('meta[name="theme-color"]').forEach((m) => {
    const scheme = m.media.includes('dark') ? 'dark' : 'light';
    m.content = COLORS[t === 'system' ? scheme : t];
  });
}
