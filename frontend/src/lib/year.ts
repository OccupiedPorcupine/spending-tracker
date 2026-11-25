/** The sheet is one file per year ("2026 Journal"). Warn before saving an entry dated in another year. */
export function sheetYear(title?: string | null): number | null {
  const m = title?.match(/\b(20\d{2})\b/);
  return m ? Number(m[1]) : null;
}

/** A warning if any of the dates falls outside the sheet's year, else null. */
export function yearWarning(title: string | null | undefined, dates: string[]): string | null {
  const y = sheetYear(title);
  if (!y) return null;
  const off = [...new Set(dates.map((d) => Number(d.slice(0, 4))).filter((d) => d && d !== y))];
  if (!off.length) return null;
  return `The date is in ${off.join(' and ')}, but this sheet is "${title}".`;
}

/** Ask before saving into the wrong year's sheet. True means go ahead. */
export function yearOk(title: string | null | undefined, dates: string[]): boolean {
  const w = yearWarning(title, dates);
  return !w || confirm(`${w} Save it there anyway?`);
}
