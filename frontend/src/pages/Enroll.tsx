import { useState } from 'react';
import { navigate } from '../App';
import { Offline } from '../lib/api';
import { enroll } from '../lib/auth';

const guessName = () => {
  const ua = navigator.userAgent;
  if (/iPhone/.test(ua)) return 'iPhone';
  if (/iPad/.test(ua)) return 'iPad';
  if (/Macintosh/.test(ua)) return 'Mac';
  return 'Passkey';
};

export function Enroll({ onDone }: { onDone: () => void }) {
  const [code, setCode] = useState('');
  const [name, setName] = useState(guessName);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  async function go(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const msg = await enroll(code.trim(), name.trim() || 'Passkey');
      if (msg) setErr(msg); else onDone();
    } catch (x) {
      setErr(x instanceof Offline ? 'Cannot reach Spend. Is Tailscale on?' : String(x));
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="lock">
      <h1>Set up</h1>
      <p className="muted">On <code>mysites</code>, run <code>docker compose exec app spend enroll</code> and enter the code it prints. It works once and lasts 15 minutes.</p>
      <form className="enroll" onSubmit={go}>
        <label>Code
          <input value={code} onChange={(e) => setCode(e.target.value.toUpperCase())} placeholder="ABCD-EFGH" required
            autoCapitalize="characters" autoCorrect="off" spellCheck={false} autoComplete="one-time-code" enterKeyHint="go" maxLength={12} />
        </label>
        <label>Name for this device
          <input value={name} onChange={(e) => setName(e.target.value)} maxLength={40}
            autoCapitalize="words" autoCorrect="off" enterKeyHint="go" />
        </label>
        <button type="submit" className="btn big" disabled={busy || code.replace(/[^A-Z0-9]/g, '').length < 8}>
          {busy ? 'Waiting for Face ID…' : 'Create passkey'}
        </button>
      </form>
      {err && <p className="err" role="alert">{err}</p>}
      <a className="muted small" href="/" onClick={(e) => { e.preventDefault(); navigate('/'); }}>Back</a>
    </main>
  );
}
