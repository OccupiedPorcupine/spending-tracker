import { useState } from 'react';
import { navigate } from '../App';
import { unlock } from '../lib/auth';
import { Offline } from '../lib/api';

/** One big button. WebAuthn only runs from a tap in Safari, so nothing is triggered on load.
 * With no passkey registered on the server yet, setting one up is the main action instead. */
export function Lock({ waiting, passkeys, onUnlocked }: { waiting: number; passkeys: number | null; onUnlocked: () => void }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const none = passkeys === 0;
  const toEnroll = (e?: React.MouseEvent) => { e?.preventDefault(); navigate('/enroll'); };
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
      {none ? (
        <>
          <p className="muted">No passkey is set up yet. Get a code with <code>spend enroll</code> on mysites, then set up this device.</p>
          <button type="button" className="btn big" onClick={() => toEnroll()}>Set up this device</button>
        </>
      ) : (
        <>
          <button type="button" className="btn big" onClick={go} disabled={busy}>{busy ? 'Waiting for Face ID…' : 'Unlock with Face ID'}</button>
          {err && (
            <p className="err" role="alert">
              {err} If iOS said there are no passkeys for this website, this device has not been set up yet:{' '}
              <a href="/enroll" onClick={toEnroll}>set it up</a>.
            </p>
          )}
          <a className="muted small" href="/enroll" onClick={toEnroll}>Set up a passkey on this device</a>
        </>
      )}
    </main>
  );
}
