/** Keypad input for an amount: digits, one point, at most 2 decimals, at most 7 whole digits. */
export function press(current: string, key: string): string {
  if (key === 'back') return current.slice(0, -1);
  if (key === 'clear') return '';
  if (key === '.') {
    if (current.includes('.')) return current;
    return (current === '' ? '0' : current) + '.';
  }
  if (!/^\d$/.test(key)) return current;
  const [whole, frac] = current.split('.');
  if (frac !== undefined) return frac.length >= 2 ? current : current + key;
  if (whole === '0') return key; // no leading zeros
  if (whole.length >= 7) return current;
  return current + key;
}

export const value = (s: string): number => (s === '' || s === '.' ? 0 : Math.round(parseFloat(s) * 100) / 100);

export const symbol = (c: string) => (c === 'MYR' ? 'RM' : 'S$');

export function money(n: number, currency = 'SGD', signed = false): string {
  const abs = Math.abs(n).toLocaleString('en-SG', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const sign = signed ? (n < 0 ? '−' : '+') : '';
  return `${sign}${symbol(currency)}${abs}`;
}
