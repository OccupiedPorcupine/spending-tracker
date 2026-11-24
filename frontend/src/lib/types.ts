export type Direction = 'expense' | 'income';
export type Currency = 'SGD' | 'MYR';

/** What the client sends to POST /api/entries. `id` is made on the device and never changes. */
export interface Entry {
  id: string;
  amount: number;
  direction: Direction;
  category: string;
  required: boolean;
  currency: Currency;
  notes: string;
  date: string; // YYYY-MM-DD
  source: 'app' | 'app-ai';
}

export interface Row {
  row: number;
  amount: number | null;
  date: string | null;
  category: string | null;
  required: string | null;
  currency: string | null;
  notes: string;
  id: string | null;
  source: string | null;
  sgd: number | null;
  editable: boolean;
}

export interface Totals { today: number; week: number; month: number; income_month: number }

export interface NoteHint { notes: string; category: string; required: boolean; currency: Currency | null; uses: number }

export interface Meta {
  categories: string[];
  notes: NoteHint[];
  last_currency: Currency;
  target: 'dev' | 'live';
  version: string;
  today: string;
}
