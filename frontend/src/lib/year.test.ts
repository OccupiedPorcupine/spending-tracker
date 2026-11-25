import { describe, expect, it } from 'vitest';
import { sheetYear, yearWarning } from './year';

describe('year warning', () => {
  it('reads the year from the title', () => {
    expect(sheetYear('2026 Journal')).toBe(2026);
    expect(sheetYear('2026 Journal (dev)')).toBe(2026);
    expect(sheetYear('Journal')).toBeNull();
    expect(sheetYear(null)).toBeNull();
  });
  it('warns only for dates outside that year', () => {
    expect(yearWarning('2026 Journal', ['2026-12-31'])).toBeNull();
    expect(yearWarning('2026 Journal', ['2027-01-01'])).toBe('The date is in 2027, but this sheet is "2026 Journal".');
    expect(yearWarning('2026 Journal', ['2026-05-01', '2025-12-31', '2025-12-30'])).toContain('2025,');
    expect(yearWarning('Journal', ['2027-01-01'])).toBeNull();
  });
});
