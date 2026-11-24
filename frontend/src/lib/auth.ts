/** Passkey flows. Both must start from a tap: Safari only allows WebAuthn after a user gesture. */
import { startAuthentication, startRegistration } from '@simplewebauthn/browser';
import { api, errorText } from './api';

type Opts = { challenge_id: string; options: any }; // eslint-disable-line @typescript-eslint/no-explicit-any

function friendly(e: unknown): string {
  const name = (e as { name?: string })?.name;
  if (name === 'NotAllowedError') return 'Cancelled, or Face ID did not match. Tap to try again.';
  if (name === 'InvalidStateError') return 'This device already has a passkey for Spend.';
  return (e as Error)?.message || 'Passkey failed';
}

export async function unlock(): Promise<string | null> {
  const o = await api<Opts>('/api/auth/login/options', { body: {} });
  if (o.status !== 200) return errorText(o.data);
  let credential;
  try {
    credential = await startAuthentication({ optionsJSON: o.data.options });
  } catch (e) {
    return friendly(e);
  }
  const v = await api('/api/auth/login/verify', { body: { challenge_id: o.data.challenge_id, credential } });
  return v.status === 200 ? null : errorText(v.data);
}

export async function enroll(code: string, name: string): Promise<string | null> {
  const o = await api<Opts>('/api/auth/register/options', { body: { code } });
  if (o.status !== 200) return errorText(o.data);
  let credential;
  try {
    credential = await startRegistration({ optionsJSON: o.data.options });
  } catch (e) {
    return friendly(e);
  }
  const v = await api('/api/auth/register/verify', { body: { challenge_id: o.data.challenge_id, credential, name } });
  return v.status === 200 ? null : errorText(v.data);
}

export const lockNow = () => api('/api/auth/logout', { body: {} });
