/** Today's date in Asia/Singapore as YYYY-MM-DD, whatever the device's time zone. */
export function todaySG(now = new Date()): string {
  const p = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Singapore', year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(now);
  const get = (t: string) => p.find((x) => x.type === t)!.value;
  return `${get('year')}-${get('month')}-${get('day')}`;
}

export function shortDate(iso: string | null, today = todaySG()): string {
  if (!iso) return 'no date';
  if (iso === today) return 'Today';
  const d = new Date(iso + 'T00:00:00');
  const t = new Date(today + 'T00:00:00');
  const diff = Math.round((t.getTime() - d.getTime()) / 86400000);
  if (diff === 1) return 'Yesterday';
  return d.toLocaleDateString('en-SG', { day: 'numeric', month: 'short', ...(d.getFullYear() !== t.getFullYear() ? { year: 'numeric' } : {}) });
}
