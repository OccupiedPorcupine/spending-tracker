import { useEffect, useState } from 'react';
import { navigate } from '../App';
import { api, errorText } from '../lib/api';
import { lockNow } from '../lib/auth';
import { Seg } from '../components/Seg';
import * as queue from '../lib/queue';
import { save } from '../lib/store';
import { currentTheme, setTheme, type Theme } from '../lib/theme';
import { money } from '../lib/amount';
import type { Favourite, Meta } from '../lib/types';

interface Status {
  sheet: { title: string | null; target: string; locale: string | null; time_zone: string | null };
  ledger: Record<string, number>;
  last_write_at: number | null;
  last_sheet_call: { ok: boolean | null; at: number | null };
  ai?: { model: string; enabled: boolean; calls: number; ok: number; input_tokens: number; output_tokens: number; cost_usd: number };
  version: string;
}
type Target = 'dev' | 'live';
interface AppSettings { require_unlock: boolean; sheet_target: Target; targets: Record<Target, boolean> }
interface Passkey { id: string; name: string; created_at: number; last_used: number | null; this_device: boolean }

const when = (ms: number | null) => (ms ? new Date(ms).toLocaleString('en-SG', { dateStyle: 'medium', timeStyle: 'short' }) : 'never');

export function Settings({ meta, items, onLocked, onSheetChanged, onMetaChanged }: {
  meta: Meta; items: queue.QItem[]; onLocked: () => void; onSheetChanged: () => void; onMetaChanged: () => void;
}) {
  const [status, setStatus] = useState<Status | null>(null);
  const [keys, setKeys] = useState<Passkey[]>([]);
  const [strict, setStrict] = useState(false);
  const [target, setTarget] = useState<Target | null>(null);
  const [targets, setTargets] = useState<Record<Target, boolean>>({ dev: true, live: false });
  const [switching, setSwitching] = useState(false);
  const [theme, setThemeState] = useState<Theme>(currentTheme);
  const [msg, setMsg] = useState<string | null>(null);
  const [favs, setFavs] = useState<Favourite[]>(meta.favourites ?? []);

  const load = async () => {
    const [s, k, st, f] = await Promise.all([
      api<Status>('/api/status'), api<{ passkeys: Passkey[] }>('/api/passkeys'), api<AppSettings>('/api/settings'),
      api<{ favourites: Favourite[] }>('/api/favourites'),
    ]);
    if (f.status === 200) setFavs(f.data.favourites);
    if (s.status === 200) setStatus(s.data);
    if (k.status === 200) setKeys(k.data.passkeys);
    if (st.status === 200) { setStrict(st.data.require_unlock); setTarget(st.data.sheet_target); setTargets(st.data.targets); }
  };
  useEffect(() => { void load().catch(() => setMsg('Could not load settings')); }, []);

  async function toggleStrict(v: boolean) {
    const r = await api<{ require_unlock: boolean }>('/api/settings', { body: { require_unlock: v } });
    if (r.status === 200) { setStrict(r.data.require_unlock); save('strict', r.data.require_unlock); }
  }

  async function switchSheet(t: Target) {
    if (t === target || switching) return;
    const text = t === 'live'
      ? 'Switch to the LIVE sheet? New entries will go into your real 2026 Journal.'
      : 'Switch to the dev copy? New entries will go into "2026 Journal (dev)", not your real sheet.';
    if (!confirm(text)) return;
    setSwitching(true);
    setMsg(null);
    try {
      const r = await api<AppSettings>('/api/settings', { body: { sheet_target: t }, timeout: 20000 });
      if (r.status === 200) { setTarget(r.data.sheet_target); onSheetChanged(); await load(); } else setMsg(errorText(r.data));
    } catch {
      setMsg('Could not switch: offline or locked.');
    } finally {
      setSwitching(false);
    }
  }

  async function removeFavourite(f: Favourite) {
    if (!confirm(`Remove the favourite "${f.name}"?`)) return;
    try {
      const r = await api<{ favourites: Favourite[] }>(`/api/favourites/${f.id}/delete`, { body: {} });
      if (r.status === 200) { setFavs(r.data.favourites); onMetaChanged(); } else setMsg(errorText(r.data));
    } catch {
      setMsg('Could not remove it: offline or locked.');
    }
  }

  async function revoke(p: Passkey) {
    const warn = p.this_device ? ' This is the passkey you are using now; you will be locked out of this device.' : '';
    if (!confirm(`Revoke "${p.name}"?${warn} You can always enrol again with spend enroll.`)) return;
    const r = await api(`/api/passkeys/${encodeURIComponent(p.id)}/revoke`, { body: {} }).catch(() => null);
    if (r && r.status !== 200) setMsg(errorText(r.data));
    void load().catch(() => onLocked());
  }

  return (
    <div className="app">
      <header className="top">
        <a className="icon-btn" href="/" onClick={(e) => { e.preventDefault(); navigate('/'); }}>← Back</a>
        <span className="brand">Settings</span>
      </header>
      <main className="page settings">
        {msg && <p className="err">{msg}</p>}
        <section className="card">
          <h2>Sheet</h2>
          {target && (
            <>
              <Seg label="Sheet to write to" value={target} onChange={(t) => void switchSheet(t)}
                options={[{ value: 'dev', label: 'Dev copy' }, ...(targets.live ? [{ value: 'live' as Target, label: 'Live sheet' }] : [])]} />
              {switching && <p className="muted small">Checking the sheet…</p>}
              {items.length > 0 && <p className="muted small">Entries waiting on this device go to whichever sheet is selected when they sync.</p>}
            </>
          )}
          <dl>
            <dt>Writing to</dt><dd><b>{status?.sheet.title ?? '…'}</b> {status?.sheet.target === 'dev' && <span className="badge queued">dev copy</span>}</dd>
            <dt>Tab</dt><dd>Daily Spending</dd>
            <dt>Last sheet call</dt><dd>{status ? (status.last_sheet_call.ok === false ? 'failed' : 'ok') + ', ' + when(status.last_sheet_call.at) : '…'}</dd>
            <dt>Last saved entry</dt><dd>{when(status?.last_write_at ?? null)}</dd>
          </dl>
        </section>
        <section className="card">
          <h2>Favourites</h2>
          {favs.length ? (
            <ul className="keys">
              {favs.map((f) => (
                <li key={f.id}>
                  <div><b>{f.name}</b>
                    <small>{f.amount === null ? 'amount typed each time' : money(f.direction === 'income' ? f.amount : -f.amount, f.currency, true)} · {f.category}{f.required ? '' : ' · extra'}{f.notes && f.notes !== f.name ? ` · "${f.notes}"` : ''}</small></div>
                  <button type="button" className="btn-ghost sm" onClick={() => void removeFavourite(f)}>Remove</button>
                </li>
              ))}
            </ul>
          ) : <p className="muted small">None yet.</p>}
          <p className="muted small">To add one, fill in the form and tap the star next to Notes. A favourite with an amount saves in one tap; without one, it fills the form and you type the amount.</p>
        </section>
        <section className="card">
          <h2>AI quick entry</h2>
          {status?.ai ? (
            <dl>
              <dt>Status</dt><dd>{status.ai.enabled ? 'on' : 'off (no API key on the server)'}</dd>
              <dt>Model</dt><dd>{status.ai.model}</dd>
              <dt>This month</dt><dd>{status.ai.calls} {status.ai.calls === 1 ? 'request' : 'requests'}{status.ai.calls > status.ai.ok ? `, ${status.ai.calls - status.ai.ok} failed` : ''}</dd>
              <dt>Tokens</dt><dd className="mono">{status.ai.input_tokens.toLocaleString()} in · {status.ai.output_tokens.toLocaleString()} out</dd>
              <dt>Est. cost</dt><dd className="mono">US${status.ai.cost_usd < 0.01 && status.ai.cost_usd > 0 ? status.ai.cost_usd.toFixed(4) : status.ai.cost_usd.toFixed(2)}</dd>
            </dl>
          ) : <p className="muted small">…</p>}
          <p className="muted small">The estimate uses the token counts OpenAI reports and the list price. Your OpenAI billing page has the exact figure.</p>
        </section>
        <section className="card">
          <h2>Appearance</h2>
          <Seg label="Theme" value={theme} onChange={(t) => { setTheme(t); setThemeState(t); }}
            options={[{ value: 'system', label: 'System' }, { value: 'light', label: 'Light' }, { value: 'dark', label: 'Dark' }]} />
          <p className="muted small">Saved on this device. System follows your phone's light or dark setting.</p>
        </section>
        <section className="card">
          <h2>Queue</h2>
          <dl>
            <dt>On this device</dt><dd>{items.length ? `${items.length} waiting` : 'empty'}{queue.lastSync ? `, last sync ${when(queue.lastSync)}` : ''}</dd>
            <dt>Server ledger</dt><dd>{status ? Object.entries(status.ledger).map(([k, v]) => `${v} ${k}`).join(', ') || 'empty' : '…'}</dd>
          </dl>
          {items.length > 0 && <button type="button" className="btn-ghost" onClick={() => void queue.flush()}>Retry now</button>}
        </section>
        <section className="card">
          <h2>Security</h2>
          <label className="switch">
            <input type="checkbox" checked={strict} onChange={(e) => void toggleStrict(e.target.checked)} />
            <span>Require Face ID every time I open the app <small className="muted">(locks after 5 minutes in the background)</small></span>
          </label>
          <h3>Passkeys</h3>
          <ul className="keys">
            {keys.map((p) => (
              <li key={p.id}>
                <div><b>{p.name}</b>{p.this_device && <span className="badge ok">this device</span>}
                  <small>added {when(p.created_at)} · last used {when(p.last_used)}</small></div>
                <button type="button" className="btn-ghost sm" onClick={() => void revoke(p)}>Revoke</button>
              </li>
            ))}
          </ul>
          <button type="button" className="btn-ghost" onClick={async () => { await lockNow().catch(() => null); onLocked(); navigate('/'); }}>Lock now</button>
        </section>
        <p className="muted small">Spend {meta.version || status?.version} · build {__APP_VERSION__}</p>
      </main>
    </div>
  );
}
