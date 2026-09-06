import { useState } from 'react';
import type { FormEvent } from 'react';
import { LockKeyhole } from 'lucide-react';
import { authRequired, confirmSignUp, getSession, isAuthConfigured, isDemoMode, signIn, signUp } from '../lib/auth';

type AuthMode = 'signin' | 'signup' | 'confirm';

export function AuthGate({ children }: { children: React.ReactNode }) {
  const [session, setSession] = useState(getSession);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [confirmationCode, setConfirmationCode] = useState('');
  const [mode, setMode] = useState<AuthMode>('signin');
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (!isAuthConfigured) {
    if (authRequired) {
      return <main className="auth-shell"><section className="auth-card"><div className="brand-mark" aria-hidden="true">DB</div><h1>Authentication is not configured</h1><p className="muted">Rebuild the dashboard with the Cognito outputs from the deployed stack before sharing this URL.</p></section></main>;
    }
    return (
      <>
        {isDemoMode && <div className="demo-banner">Demo mode — configure Cognito for authenticated access</div>}
        {children}
      </>
    );
  }

  if (session) return <>{children}</>;

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      if (mode === 'confirm') {
        await confirmSignUp(username, confirmationCode);
        setMode('signin');
        setNotice('Email confirmed. Sign in with your new account.');
      } else if (mode === 'signup') {
        const confirmed = await signUp(username, password);
        if (confirmed) {
          setSession(await signIn(username, password));
        } else {
          setMode('confirm');
          setNotice('Check your email for the confirmation code.');
        }
      } else {
        setSession(await signIn(username, password));
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Sign-in failed.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="auth-shell">
      <form className="auth-card" onSubmit={submit}>
        <div className="brand-mark" aria-hidden="true">DB</div>
        <p className="eyebrow">Deadbolt</p>
        <h1>{mode === 'signup' ? 'Create your operator account' : mode === 'confirm' ? 'Confirm your email' : 'Sign in to the entitlement register'}</h1>
        <p className="muted">{mode === 'signup' ? 'Use your work email to create an operator account.' : mode === 'confirm' ? 'Enter the confirmation code sent to your email.' : 'Use your Deadbolt operator account.'}</p>
        <label>Email<input autoComplete="username" type="email" value={username} onChange={(event) => setUsername(event.target.value)} required disabled={mode === 'confirm'} /></label>
        {mode === 'confirm' ? <label>Confirmation code<input inputMode="numeric" value={confirmationCode} onChange={(event) => setConfirmationCode(event.target.value)} required /></label> : <label>Password<input autoComplete={mode === 'signup' ? 'new-password' : 'current-password'} type="password" value={password} onChange={(event) => setPassword(event.target.value)} required /></label>}
        {error && <p className="error-message" role="alert">{error}</p>}
        {notice && <p className="muted" role="status">{notice}</p>}
        <button className="primary-button" type="submit" disabled={busy}>{busy ? 'Working…' : mode === 'signup' ? 'Create account' : mode === 'confirm' ? 'Confirm email' : 'Sign in'}</button>
        {mode !== 'confirm' && <button className="auth-switch" type="button" onClick={() => { setMode(mode === 'signin' ? 'signup' : 'signin'); setError(null); setNotice(null); }}>{mode === 'signin' ? 'Create a new account' : 'Already have an account? Sign in'}</button>}
        <p className="auth-note"><LockKeyhole size={14} /> Session tokens stay in this browser session.</p>
      </form>
    </main>
  );
}
