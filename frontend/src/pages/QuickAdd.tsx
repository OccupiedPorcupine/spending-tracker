import { useEffect, useMemo, useRef, useState } from 'react';
import { navigate, type Phase, type Recent } from '../App';
import { AiBox, Drafts } from '../components/AiBox';
import { GearIcon, StarIcon } from '../components/Icons';
import { Keypad } from '../components/Keypad';
import { Seg } from '../components/Seg';
import { api, errorText, Locked } from '../lib/api';
import { money, press, symbol, value } from '../lib/amount';
import { shortDate, todaySG } from '../lib/dates';
import * as queue from '../lib/queue';
import { load, save } from '../lib/store';
import type { Currency, Direction, Draft, Entry, Favourite, Meta, Row } from '../lib/types';
import { yearOk, yearWarning } from '../lib/year';

interface Props { phase: Phase; meta: Meta; recent: Recent | null; items: queue.QItem[]; onChanged: () => void }

/** "4.5" for 4.5, "12" for 12: a keypad string for an amount. */
const keyed = (n: number) => String(Math.round(Math.abs(n) * 100) / 100);

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
  const [fromAi, setFromAi] = useState(false); // the form was filled by the AI box: the row gets "app-ai"
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [msg, setMsg] = useState<{ text: string; bad?: boolean } | null>(null);
  const [editing, setEditing] = useState<Row | null>(null);
  const [busy, setBusy] = useState(false);
  const [starred, setStarred] = useState(false);
  const notesRef = useRef<HTMLInputElement>(null);

  useEffect(() => queue.onSaved(({ id }) => setSaved((s) => new Set(s).add(id))), []);
  // the date default rolls over at midnight Singapore time if the app stays open
  useEffect(() => {
    const t = setInterval(() => setDate((d) => (d < todaySG() && !document.hasFocus() ? todaySG() : d)), 60000);
    return () => clearInterval(t);
  }, []);

  // After edit mode has re-laid the page out (the AI box and favourites go away), bring the form into view.
  const editingId = editing?.id;
  useEffect(() => { if (editingId) window.scrollTo({ top: 0, behavior: 'smooth' }); }, [editingId]);

  const cats = meta.categories.filter((c) => (direction === 'income' ? true : c !== 'Revenue'));
  const favs = meta.favourites ?? [];
  const amt = value(amount);
  const canSave = amt > 0 && !!category && !!date;
  const yearNote = yearWarning(meta.sheet_title, [date]);

  const hints = useMemo(() => {
    if (!noteFocus) return [];
    const q = notes.trim().toLowerCase();
    if (!q) return meta.notes.slice(0, 4); // most used first
    const starts = meta.notes.filter((n) => n.notes.toLowerCase().startsWith(q));
    const words = meta.notes.filter((n) => !n.notes.toLowerCase().startsWith(q) && n.notes.toLowerCase().includes(' ' + q));
    return [...starts, ...words].filter((n) => n.notes.toLowerCase() !== q).slice(0, 4);
  }, [notes, noteFocus, meta.notes]);

  const say = (text: string, bad = false) => setMsg({ text, bad });
  const showFlash = (text: string) => { setFlash(text); setTimeout(() => setFlash(null), 1600); };

  function chooseDirection(d: Direction) {
    setDirection(d);
    if (d === 'income') { setCategory('Revenue'); setRequired(true); } else if (category === 'Revenue') setCategory(null);
  }

  // Editing an old row must not change the default currency for new entries.
  function chooseCurrency(c: Currency) { setCurrency(c); if (!editing) save('currency', c); }

  function pickHint(h: Meta['notes'][number]) {
    setNotes(h.notes);
    setCategory(h.category);
    setRequired(h.required);
    if (h.currency) chooseCurrency(h.currency);
    setDirection(h.category === 'Revenue' ? 'income' : 'expense');
    notesRef.current?.blur();
  }

  function reset() {
    setAmount(''); setCategory(null); setNotes(''); setDirection('expense'); setRequired(true); setDate(todaySG());
    setCurrency(load('currency', meta.last_currency || 'SGD'));
    setFromAi(false); setMsg(null); setEditing(null); setStarred(false);
    notesRef.current?.blur();
  }

  function fill(d: Draft) {
    setEditing(null);
    setAmount(d.amount ? String(d.amount) : '');
    setDirection(d.direction);
    setCategory(d.category);
    setRequired(d.required);
    setCurrency(d.currency);
    setNotes(d.notes);
    setDate(d.date);
    setFromAi(true);
    setMsg(d.amount ? null : { text: 'Enter the amount, then save.' });
  }

  function fallback(text: string, why: string) {
    setNotes(text.slice(0, 120));
    setFromAi(false);
    say(`${why} Your text is in Notes; fill in the rest.`);
  }

  function editDraft(i: number) {
    fill(drafts[i]);
    setDrafts(drafts.filter((_, j) => j !== i));
  }

  async function saveDrafts() {
    if (!yearOk(meta.sheet_title, drafts.map((d) => d.date))) return;
    const entries: Entry[] = drafts.map((d) => ({
      id: crypto.randomUUID(), amount: d.amount!, direction: d.direction, category: d.category!, required: d.required,
      currency: d.currency, notes: d.notes, date: d.date, source: 'app-ai',
    }));
    for (const e of entries) await queue.enqueue(e);
    setDrafts([]);
    setJustSaved(entries[entries.length - 1].id);
    showFlash(`${entries.length} entries`);
    void queue.flush().catch(() => undefined);
  }

  async function add(entry: Entry) {
    await queue.enqueue(entry);
    setJustSaved(entry.id);
    showFlash(`${money(entry.direction === 'expense' ? -entry.amount : entry.amount, entry.currency, true)} ${entry.notes || entry.category}`);
    void queue.flush().catch(() => undefined);
  }

  async function submit() {
    if (!canSave || busy) return;
    if (!yearOk(meta.sheet_title, [date])) return;
    if (editing) { await saveEdit(); return; }
    const entry: Entry = {
      id: crypto.randomUUID(), amount: amt, direction, category: category!, required, currency,
      notes: notes.trim(), date, source: fromAi ? 'app-ai' : 'app',
    };
    save('currency', currency);
    reset();
    await add(entry);
  }

  /** A favourite with an amount saves at once (dated as the form's date); one without fills the form. */
  async function logFavourite(f: Favourite) {
    if (f.amount === null) {
      setEditing(null); setAmount(''); setDirection(f.direction); setCategory(f.category); setRequired(f.required);
      setCurrency(f.currency); setNotes(f.notes); setFromAi(false);
      say(`${f.name}: enter the amount, then save.`);
      return;
    }
    if (!yearOk(meta.sheet_title, [date])) return;
    const entry: Entry = {
      id: crypto.randomUUID(), amount: f.amount, direction: f.direction, category: f.category, required: f.required,
      currency: f.currency, notes: f.notes, date, source: 'app',
    };
    setMsg(null);
    await add(entry);
  }

  async function addFavourite() {
    if (!category || busy) return;
    const name = (notes.trim() || category).slice(0, 40);
    try {
      const r = await api('/api/favourites', {
        body: { name, amount: amt > 0 ? amt : null, direction, category, required, currency, notes: notes.trim() },
      });
      if (r.status !== 200) { say(`Couldn't add the favourite: ${errorText(r.data)}`, true); return; }
      setStarred(true);
      say(amt > 0
        ? `Added "${name}, ${money(amt, currency)}" to favourites. Tap it above to log it in one go.`
        : `Added "${name}" to favourites. Tapping it fills the form; you add the amount.`);
      onChanged();
    } catch (e) {
      if (!(e instanceof Locked)) say('Offline: favourites can be added when you are connected.', true);
    }
  }

  function startEdit(r: Row) {
    if (r.amount === null) return;
    setDrafts([]);
    setEditing(r);
    setAmount(keyed(r.amount));
    setDirection(r.amount > 0 ? 'income' : 'expense');
    setCategory(r.category);
    setRequired(r.required !== 'No');
    setCurrency(r.currency === 'MYR' ? 'MYR' : 'SGD');
    setNotes(r.notes);
    setDate(r.date ?? todaySG());
    setFromAi(false); setStarred(false);
    setMsg(null);
  }

  async function saveEdit() {
    if (!editing?.id) return;
    setBusy(true);
    try {
      const r = await api(`/api/entries/${editing.id}/edit`, {
        body: { amount: amt, direction, category, required, currency, notes: notes.trim(), date }, timeout: 15000,
      });
      if (r.status !== 200) { say(`Couldn't save the change: ${errorText(r.data)}`, true); return; }
      reset();
      showFlash('changes');
      onChanged();
    } catch (e) {
      if (!(e instanceof Locked)) say('Offline: changes to saved rows need a connection. Try again when connected.', true);
    } finally {
      setBusy(false);
    }
  }

  async function deleteEditing() {
    if (!editing?.id || busy) return;
    if (!confirm(`Delete "${editing.notes || editing.category}" (${money(editing.amount ?? 0, editing.currency ?? 'SGD', true)}) from the sheet?`)) return;
    setBusy(true);
    try {
      const r = await api(`/api/entries/${editing.id}/delete`, { body: {} });
      if (r.status !== 200) { say(`Couldn't delete it: ${errorText(r.data)}`, true); return; }
      reset();
      onChanged();
    } catch (e) {
      if (!(e instanceof Locked)) say('Offline: deleting a saved row needs a connection.', true);
    } finally {
      setBusy(false);
    }
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

  async function discard(i: queue.QItem) {
    if (!confirm(`Discard "${i.entry.notes || i.entry.category}"? It won't be saved.`)) return;
    await queue.discard(i.entry.id);
    void queue.flush().catch(() => undefined);
  }

  const waiting = items.length;
  const stuck = items.filter((i) => i.state === 'failed').length;
  const status = queue.needsUnlock ? 'Unlock to sync'
    : phase === 'offline' ? (waiting ? `Offline · ${waiting} waiting` : 'Offline')
    : stuck ? `${stuck} failed`
    : items.some((i) => i.state === 'queued') ? `${waiting} queued` : waiting ? `${waiting} saving…` : null;

  const signed = direction === 'expense' ? -amt : amt;
  const label = flash ? `Saved ${flash}`
    : busy ? 'Saving…'
    : !amt ? 'Enter an amount' : !category ? 'Pick a category'
    : editing ? `Save changes · ${money(signed, currency, true)}` : `Save ${money(signed, currency, true)}`;

  return (
    <div className="app">
      <header className="top">
        <span className="brand">Spend{meta.target === 'dev' && <small className="dev">dev sheet</small>}</span>
        {status && <span className={'sync ' + (stuck || queue.needsUnlock ? 'bad' : '')} role="status">{status}</span>}
        {phase === 'open' && <a className="icon-btn" href="/settings" onClick={(e) => { e.preventDefault(); navigate('/settings'); }} aria-label="Settings"><GearIcon /></a>}
      </header>

      <main className="page">
        {recent && (
          <div className="totals" aria-label="Spending in SGD">
            <div><span>Today</span><b className="mono">{money(recent.totals.today)}</b></div>
            <div><span>Week</span><b className="mono">{money(recent.totals.week)}</b></div>
            <div><span>Month</span><b className="mono">{money(recent.totals.month)}</b></div>
          </div>
        )}

        {!editing && phase === 'open' && meta.ai !== false && (
          <AiBox onFill={fill} onDrafts={(d) => { setDrafts(d); setMsg(null); }} onFallback={fallback} />
        )}
        {!editing && favs.length > 0 && (
          <div className="favs" role="group" aria-label="Favourites">
            {favs.map((f) => (
              <button type="button" key={f.id} className="fav" onClick={() => void logFavourite(f)}
                aria-label={f.amount === null ? `Fill in ${f.name}` : `Save ${f.name}, ${money(f.amount, f.currency)}`}>
                <b>{f.name}</b>
                <small className="mono">{f.amount === null ? `${symbol(f.currency)} …` : money(f.direction === 'income' ? f.amount : -f.amount, f.currency, f.direction === 'income')}</small>
              </button>
            ))}
          </div>
        )}
        {msg && <p className={'notice ai-msg' + (msg.bad ? ' bad' : '')} role="status">{msg.text}</p>}
        {drafts.length > 0 && (
          <Drafts drafts={drafts} onSaveAll={() => void saveDrafts()} onEdit={editDraft} onClose={() => setDrafts([])} />
        )}

        <form className={'form' + (editing ? ' editing' : '')} onSubmit={(e) => { e.preventDefault(); void submit(); }}>
          {editing && (
            <div className="edit-bar">
              <div><b>Editing</b><small>row {editing.row} · {editing.notes || editing.category}</small></div>
              <button type="button" className="btn-ghost sm danger" onClick={() => void deleteEditing()} disabled={busy}>Delete</button>
              <button type="button" className="btn-ghost sm" onClick={reset} disabled={busy}>Cancel</button>
            </div>
          )}
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
          {yearNote && <p className="notice warn" role="status">{yearNote} You'll be asked before it saves.</p>}

          <div className="notes">
            <div className="notes-row">
              <input
                ref={notesRef} type="text" placeholder="Notes (e.g. Chicken rice)" value={notes} maxLength={120}
                autoCapitalize="sentences" autoCorrect="on" spellCheck enterKeyHint="done" autoComplete="off"
                onChange={(e) => { setNotes(e.target.value); setStarred(false); }} onFocus={() => setNoteFocus(true)}
                onBlur={() => setTimeout(() => setNoteFocus(false), 150)}
                onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); (e.target as HTMLInputElement).blur(); } }}
                aria-label="Notes"
              />
              {!editing && (
                <button type="button" className="star" onClick={() => void addFavourite()} disabled={!category}
                  aria-label="Save this as a favourite" title="Save as favourite" aria-pressed={starred}>
                  <StarIcon filled={starred} />
                </button>
              )}
            </div>
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
            <button type="submit" className={'btn save' + (flash ? ' done' : '')} disabled={(!canSave || busy) && !flash}>{label}</button>
          </div>
        </form>

        <RecentList phase={phase} recent={recent} items={items} justSaved={justSaved} saved={saved} editingId={editing?.id ?? null}
          onUndo={undo} onEdit={startEdit} onDiscard={discard} />
      </main>
    </div>
  );
}

function RecentList({ phase, recent, items, justSaved, saved, editingId, onUndo, onEdit, onDiscard }: {
  phase: Phase; recent: Recent | null; items: queue.QItem[]; justSaved: string | null; saved: Set<string>; editingId: string | null;
  onUndo: (id: string) => void; onEdit: (r: Row) => void; onDiscard: (i: queue.QItem) => void;
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
              {i.entry.id === justSaved
                ? <button type="button" className="link" onClick={() => onUndo(i.entry.id)}>Undo</button>
                : i.state === 'failed' && <button type="button" className="link" onClick={() => onDiscard(i)}>Discard</button>}
            </div>
            {i.state === 'failed' && i.error && <p className="err">{i.error}</p>}
          </li>
        ))}
        {recent?.rows.filter((r) => !r.id || !inQueue.has(r.id)).map((r) => <RowItem key={r.row + (r.id ?? '')} r={r} today={today}
          saved={!!r.id && saved.has(r.id)} canUndo={!!r.id && r.id === justSaved && r.editable} editing={!!r.id && r.id === editingId}
          onUndo={onUndo} onEdit={onEdit} />)}
      </ul>
      {!recent && (
        <p className="notice">
          {phase === 'offline'
            ? 'Offline. New entries stay on this phone and sync when you are back on the tailnet.'
            : 'Loading recent rows…'}
        </p>
      )}
      {recent && recent.rows.some((r) => r.editable) && (
        <p className="muted small">Rows this app saved can be edited. Rows typed into the sheet are read-only here.</p>
      )}
      {recent && recent.rows_left < 60 && (
        <p className="notice warn">Only {recent.rows_left} rows left before row 1001, where the Dashboard's ranges end.</p>
      )}
    </section>
  );
}

function RowItem({ r, today, saved, canUndo, editing, onUndo, onEdit }: {
  r: Row; today: string; saved: boolean; canUndo: boolean; editing: boolean; onUndo: (id: string) => void; onEdit: (r: Row) => void;
}) {
  const blank = !r.date && !r.category;
  return (
    <li className={(blank ? 'blank' : '') + (editing ? ' editing' : '')}>
      <div className="what">
        <b>{r.notes || r.category || 'Amount only'}</b>
        <small>{blank ? `row ${r.row} · not filled in` : `${shortDate(r.date, today)} · ${r.category ?? ''}${r.required === 'No' ? ' · extra' : ''}`}</small>
      </div>
      <div className="right">
        {r.amount !== null && <span className={'mono amt' + (r.amount > 0 ? ' pos' : '')}>{money(r.amount, r.currency ?? 'SGD', true)}</span>}
        {saved && <span className="badge ok">saved</span>}
        {canUndo && <button type="button" className="link" onClick={() => onUndo(r.id!)}>Undo</button>}
        {r.editable && !canUndo && <button type="button" className="link" onClick={() => onEdit(r)} aria-label={`Edit ${r.notes || r.category}`}>Edit</button>}
      </div>
    </li>
  );
}
