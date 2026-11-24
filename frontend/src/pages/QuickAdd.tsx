import { useEffect, useMemo, useRef, useState } from 'react';
import { navigate, type Phase, type Recent } from '../App';
import { Keypad } from '../components/Keypad';
import { Seg } from '../components/Seg';
import { api, errorText } from '../lib/api';
import { money, press, symbol, value } from '../lib/amount';
import { shortDate, todaySG } from '../lib/dates';
import * as queue from '../lib/queue';
import { load, save } from '../lib/store';
import type { Currency, Direction, Entry, Meta, Row } from '../lib/types';

interface Props { phase: Phase; meta: Meta; recent: Recent | null; items: queue.QItem[]; onChanged: () => void }

export function QuickAdd({ phase, meta, recent, items, onChanged }: Props) {
  const [amount, setAmount] = useState('');
  const [direction, setDirection] = useState<Direction>('expense');
  const [category, setCategory] = useState<string | null>(null);
  const [required, setRequired] = useState(true);
  const [currency, setCurrency] = useState<Currency>(() => load('currency', meta.last_currency || 'SGD'));
  const [notes, setNotes] = useState('');
  const [date, setDate] = useState(todaySG());
  const [flash, setFlash] = useState<string | null>(null);
  const [justSaved, setJustSaved] = useState<string | null>(null);
  const [saved, setSaved] = useState<Set<string>>(new Set());
  const [noteFocus, setNoteFocus] = useState(false);
  const notesRef = useRef<HTMLInputElement>(null);

  useEffect(() => queue.onSaved(({ id }) => setSaved((s) => new Set(s).add(id))), []);
  // the date default rolls over at midnight Singapore time if the app stays open
  useEffect(() => {
    const t = setInterval(() => setDate((d) => (d < todaySG() && !document.hasFocus() ? todaySG() : d)), 60000);
    return () => clearInterval(t);
  }, []);

  const cats = meta.categories.filter((c) => (direction === 'income' ? true : c !== 'Revenue'));
  const amt = value(amount);
  const canSave = amt > 0 && !!category && !!date;

  const hints = useMemo(() => {
    const q = notes.trim().toLowerCase();
    if (!q || !noteFocus) return [];
    return meta.notes.filter((n) => n.notes.toLowerCase().startsWith(q) && n.notes.toLowerCase() !== q).slice(0, 4);
  }, [notes, noteFocus, meta.notes]);

  function chooseDirection(d: Direction) {
    setDirection(d);
    if (d === 'income') { setCategory('Revenue'); setRequired(true); } else if (category === 'Revenue') setCategory(null);
  }

  function chooseCurrency(c: Currency) { setCurrency(c); save('currency', c); }

  function pickHint(h: Meta['notes'][number]) {
    setNotes(h.notes);
    setCategory(h.category);
    setRequired(h.required);
    if (h.currency) chooseCurrency(h.currency);
    setDirection(h.category === 'Revenue' ? 'income' : 'expense');
    notesRef.current?.blur();
  }

  async function submit() {
    if (!canSave) return;
    const entry: Entry = {
      id: crypto.randomUUID(), amount: amt, direction, category: category!, required, currency,
      notes: notes.trim(), date, source: 'app',
    };
    await queue.enqueue(entry);
    save('currency', currency);
    setJustSaved(entry.id);
    setFlash(`${money(direction === 'expense' ? -amt : amt, currency, true)} ${category}`);
    setTimeout(() => setFlash(null), 1600);
    setAmount(''); setCategory(null); setNotes(''); setDirection('expense'); setRequired(true); setDate(todaySG());
    notesRef.current?.blur();
    void queue.flush().catch(() => undefined);
  }

  async function undo(id: string) {
    if (items.some((i) => i.entry.id === id)) {
      await queue.discard(id);
      void queue.flush().catch(() => undefined);
    } else {
      try {
        const r = await api(`/api/entries/${id}/delete`, { body: {} });
        if (r.status !== 200) { alert(`Could not undo: ${errorText(r.data)}`); return; }
      } catch {
        alert('Could not undo: offline or locked. Try again when connected.');
        return;
      }
    }
    setJustSaved(null);
    onChanged();
  }

  const waiting = items.length;
  const stuck = items.filter((i) => i.state === 'failed').length;
  const status = queue.needsUnlock ? 'Unlock to sync'
    : phase === 'offline' ? (waiting ? `Offline · ${waiting} waiting` : 'Offline')
    : stuck ? `${stuck} failed`
    : items.some((i) => i.state === 'queued') ? `${waiting} queued` : waiting ? `${waiting} saving…` : null;

  return (
    <div className="app">
      <header className="top">
        <span className="brand">Spend{meta.target === 'dev' && <small className="dev">dev sheet</small>}</span>
        {status && <span className={'sync ' + (stuck || queue.needsUnlock ? 'bad' : '')} role="status">{status}</span>}
        {phase === 'open' && <a className="icon-btn" href="/settings" onClick={(e) => { e.preventDefault(); navigate('/settings'); }} aria-label="Settings">⚙</a>}
      </header>

      <main className="page">
        {recent && (
          <div className="totals" aria-label="Spending in SGD">
            <div><span>Today</span><b className="mono">{money(recent.totals.today)}</b></div>
            <div><span>Week</span><b className="mono">{money(recent.totals.week)}</b></div>
            <div><span>Month</span><b className="mono">{money(recent.totals.month)}</b></div>
          </div>
        )}

        <form className="form" onSubmit={(e) => { e.preventDefault(); void submit(); }}>
          <div className="row2">
            <Seg label="Expense or income" value={direction} onChange={chooseDirection}
              options={[{ value: 'expense', label: 'Expense' }, { value: 'income', label: 'Income' }]} />
            <Seg label="Currency" value={currency} onChange={chooseCurrency}
              options={[{ value: 'SGD', label: 'SGD' }, { value: 'MYR', label: 'MYR' }]} />
          </div>

          <output className={'amount' + (amount ? '' : ' empty') + (direction === 'income' ? ' income' : '')} aria-live="polite" aria-label="Amount">
            <span className="cur">{symbol(currency)}</span>{amount || '0.00'}
          </output>

          <div className="chips" role="group" aria-label="Category">
            {cats.map((c) => (
              <button type="button" key={c} className="chip-btn" aria-pressed={category === c} onClick={() => setCategory(c)}>{c}</button>
            ))}
          </div>

          <div className="row3">
            <Seg label="Required" value={required ? 'yes' : 'no'} onChange={(v) => setRequired(v === 'yes')}
              options={[{ value: 'yes', label: 'Required' }, { value: 'no', label: 'Extra' }]} />
            <label className="date">
              <span className="sr">Date</span>
              <input type="date" value={date} max="2099-12-31" onChange={(e) => setDate(e.target.value)} required />
            </label>
          </div>

          <div className="notes">
            <input
              ref={notesRef} type="text" placeholder="Notes (e.g. Chicken rice)" value={notes} maxLength={120}
              autoCapitalize="sentences" autoCorrect="on" spellCheck enterKeyHint="done" autoComplete="off"
              onChange={(e) => setNotes(e.target.value)} onFocus={() => setNoteFocus(true)}
              onBlur={() => setTimeout(() => setNoteFocus(false), 150)}
              onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); (e.target as HTMLInputElement).blur(); } }}
              aria-label="Notes"
            />
            {hints.length > 0 && (
              <div className="hints">
                {hints.map((h) => (
                  <button type="button" key={h.notes} onPointerDown={(e) => e.preventDefault()} onClick={() => pickHint(h)}>
                    {h.notes} <small>{h.category} · {h.currency ?? ''}</small>
                  </button>
                ))}
              </div>
            )}
          </div>

          <Keypad onKey={(k) => setAmount((a) => press(a, k))} />

          <div className="savebar">
            <button type="submit" className={'btn save' + (flash ? ' done' : '')} disabled={!canSave && !flash}>
              {flash ? `Saved ${flash}` : canSave ? `Save ${money(direction === 'expense' ? -amt : amt, currency, true)}` : !amt ? 'Enter an amount' : 'Pick a category'}
            </button>
          </div>
        </form>

        <RecentList phase={phase} recent={recent} items={items} justSaved={justSaved} saved={saved} onUndo={undo} />
      </main>
    </div>
  );
}

function RecentList({ phase, recent, items, justSaved, saved, onUndo }: {
  phase: Phase; recent: Recent | null; items: queue.QItem[]; justSaved: string | null; saved: Set<string>;
  onUndo: (id: string) => void;
}) {
  const today = todaySG();
  const inQueue = new Set(items.map((i) => i.entry.id));
  return (
    <section className="recent" aria-label="Recent rows">
      <h2>Recent</h2>
      <ul>
        {[...items].reverse().map((i) => (
          <li key={i.entry.id} className="pending">
            <div className="what">
              <b>{i.entry.notes || i.entry.category}</b>
              <small>{shortDate(i.entry.date, today)} · {i.entry.category}</small>
            </div>
            <div className="right">
              <span className={'mono amt' + (i.entry.direction === 'income' ? ' pos' : '')}>
                {money(i.entry.direction === 'expense' ? -i.entry.amount : i.entry.amount, i.entry.currency, true)}
              </span>
              <span className={'badge ' + i.state}>{i.state === 'saving' ? 'saving…' : i.state === 'queued' ? 'queued' : 'failed'}</span>
              {i.entry.id === justSaved && <button type="button" className="link" onClick={() => onUndo(i.entry.id)}>Undo</button>}
            </div>
            {i.state === 'failed' && i.error && <p className="err">{i.error}</p>}
          </li>
        ))}
        {recent?.rows.filter((r) => !r.id || !inQueue.has(r.id)).map((r) => <RowItem key={r.row + (r.id ?? '')} r={r} today={today}
          saved={!!r.id && saved.has(r.id)} canUndo={!!r.id && r.id === justSaved && r.editable} onUndo={onUndo} />)}
      </ul>
      {!recent && (
        <p className="notice">
          {phase === 'offline'
            ? 'Offline. New entries stay on this phone and sync when you are back on the tailnet.'
            : 'Loading recent rows…'}
        </p>
      )}
      {recent && recent.rows_left < 60 && (
        <p className="notice warn">Only {recent.rows_left} rows left before row 1001, where the Dashboard's ranges end.</p>
      )}
    </section>
  );
}

function RowItem({ r, today, saved, canUndo, onUndo }: { r: Row; today: string; saved: boolean; canUndo: boolean; onUndo: (id: string) => void }) {
  const blank = !r.date && !r.category;
  return (
    <li className={blank ? 'blank' : ''}>
      <div className="what">
        <b>{r.notes || r.category || 'Amount only'}</b>
        <small>{blank ? `row ${r.row} · not filled in` : `${shortDate(r.date, today)} · ${r.category ?? ''}${r.required === 'No' ? ' · extra' : ''}`}</small>
      </div>
      <div className="right">
        {r.amount !== null && <span className={'mono amt' + (r.amount > 0 ? ' pos' : '')}>{money(r.amount, r.currency ?? 'SGD', true)}</span>}
        {saved && <span className="badge ok">saved</span>}
        {canUndo && <button type="button" className="link" onClick={() => onUndo(r.id!)}>Undo</button>}
      </div>
    </li>
  );
}
