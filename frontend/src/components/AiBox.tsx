import { useState } from 'react';
import { api, Locked } from '../lib/api';
import { money } from '../lib/amount';
import { shortDate, todaySG } from '../lib/dates';
import type { Draft } from '../lib/types';

interface Props {
  /** One item: fill the form with it. */
  onFill: (d: Draft) => void;
  /** Several items: show them as a list to save together or edit one by one. */
  onDrafts: (d: Draft[]) => void;
  /** Anything went wrong: the typed text goes into Notes and the manual form takes over. */
  onFallback: (text: string, why: string) => void;
}

/** "Type it" box: a short phrase like "grab 12 rm" or "lunch 5.20 and coffee 1.10" fills the form.
 * The AI only drafts; nothing is saved until Save is tapped. */
export function AiBox({ onFill, onDrafts, onFallback }: Props) {
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);

  async function go() {
    const t = text.trim();
    if (!t || busy) return;
    setBusy(true);
    (document.activeElement as HTMLElement | null)?.blur();
    try {
      const r = await api<{ items: Draft[] } & { error?: string }>('/api/parse', { body: { text: t }, timeout: 10000 });
      if (r.status === 200 && r.data.items.length) {
        if (r.data.items.length === 1) onFill(r.data.items[0]); else onDrafts(r.data.items);
        setText('');
      } else {
        onFallback(t, r.status === 429 ? 'Too many tries; wait a minute.' : "Couldn't read that.");
        setText('');
      }
    } catch (e) {
      if (!(e instanceof Locked)) { onFallback(t, 'Offline.'); setText(''); }
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="ai" onSubmit={(e) => { e.preventDefault(); void go(); }}>
      <input
        type="text" value={text} maxLength={300} placeholder='Type it: "grab 12 rm", "kopi 1.40"'
        autoCapitalize="sentences" autoCorrect="on" spellCheck enterKeyHint="done" autoComplete="off"
        onChange={(e) => setText(e.target.value)} aria-label="Type an entry in words" disabled={busy}
      />
      <button type="submit" className="btn-ghost" disabled={!text.trim() || busy}>{busy ? 'Reading…' : 'Fill'}</button>
    </form>
  );
}

/** The list shown when one phrase held several items. */
export function Drafts({ drafts, onSaveAll, onEdit, onClose }: {
  drafts: Draft[]; onSaveAll: () => void; onEdit: (i: number) => void; onClose: () => void;
}) {
  const today = todaySG();
  const ready = drafts.every((d) => d.amount && d.category);
  return (
    <section className="drafts" aria-label="Entries read from your text">
      <ul>
        {drafts.map((d, i) => (
          <li key={i}>
            <div className="what">
              <b>{d.notes || d.category || 'Entry'}</b>
              <small>
                {d.category ?? 'pick a category'}{d.required ? '' : ' · extra'}
                {d.date !== today ? ` · ${shortDate(d.date, today)}` : ''}
              </small>
            </div>
            <div className="right">
              <span className={'mono amt' + (d.direction === 'income' ? ' pos' : '')}>
                {d.amount ? money(d.direction === 'expense' ? -d.amount : d.amount, d.currency, true) : 'no amount'}
              </span>
              <button type="button" className="link" onClick={() => onEdit(i)}>Edit</button>
            </div>
          </li>
        ))}
      </ul>
      <div className="drafts-actions">
        <button type="button" className="btn-ghost" onClick={onClose}>Discard</button>
        <button type="button" className="btn" disabled={!ready} onClick={onSaveAll}>
          {ready ? `Save all ${drafts.length}` : 'Edit the incomplete one'}
        </button>
      </div>
    </section>
  );
}
