import { useState } from 'react';
import { navigate } from '../App';
import { unlock } from '../lib/auth';
import { Offline } from '../lib/api';

/** One big button. WebAuthn only runs from a tap in Safari, so nothing is triggered on load. */
export function Lock({ waiting, onUnlocked }: { waiting: number; onUnlocked: () => void }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  async function go() {
    setBusy(true);
    setErr(null);
    try {
      const e = await unlock();
      if (e) setErr(e); else onUnlocked();
    } catch (e) {
      setErr(e instanceof Offline ? 'Cannot reach Spend. Is Tailscale on?' : String(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="lock">
      <h1>Spend</h1>
      {waiting > 0 && <p className="notice warn">{waiting} {waiting === 1 ? 'entry is' : 'entries are'} waiting on this device. Unlock to sync.</p>}
      <button type="button" className="btn big" onClick={go} disabled={busy}>{busy ? 'Waiting for Face ID…' : 'Unlock with Face ID'}</button>
      {err && <p className="err" role="alert">{err}</p>}
      <a className="muted small" href="/enroll" onClick={(e) => { e.preventDefault(); navigate('/enroll'); }}>Set up a passkey on this device</a>
    </main>
  );
}
