import React, { useEffect, useState } from 'react';
import App from '../App.jsx';
import UserAccess from './UserAccess.jsx';
import Sessions from './Sessions.jsx';
import './AuthGate.css';

const API = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '');
const AUTH = `${API}/api/v1/auth`;
let csrfToken = '';

function readCookie(name) {
  const prefix = `${name}=`;
  const part = document.cookie.split('; ').find((item) => item.startsWith(prefix));
  return part ? decodeURIComponent(part.slice(prefix.length)) : '';
}

// Existing application components use fetch directly. Attach the session cookie and
// signed double-submit CSRF header centrally without changing each API call site.
if (typeof window !== 'undefined' && !window.__tradingPlatformFetchWrapped) {
  const originalFetch = window.fetch.bind(window);
  window.fetch = (input, init = {}) => {
    const rawUrl = typeof input === 'string' ? input : input.url;
    let requestUrl;
    try { requestUrl = new URL(rawUrl, window.location.origin); } catch { return originalFetch(input, init); }
    if (!requestUrl.pathname.startsWith('/api/v1/')) return originalFetch(input, init);
    const headers = new Headers(init.headers || (input instanceof Request ? input.headers : undefined));
    const method = String(init.method || (input instanceof Request ? input.method : 'GET')).toUpperCase();
    if (window.__tpUserRole === 'VIEWER' && !['GET', 'HEAD', 'OPTIONS'].includes(method)) {
      return Promise.resolve(new Response(JSON.stringify({ detail: 'Viewer accounts have read-only access' }), {
        status: 403, headers: { 'Content-Type': 'application/json' },
      }));
    }
    if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) {
      const csrf = csrfToken || readCookie('tp_csrf');
      if (csrf) headers.set('X-CSRF-Token', csrf);
    }
    return originalFetch(input, { ...init, headers, credentials: 'include' }).then(async (response) => {
      if (response.ok && (requestUrl.pathname.endsWith('/auth/csrf') || requestUrl.pathname.endsWith('/auth/login'))) {
        try {
          const payload = await response.clone().json();
          if (payload.csrf_token) csrfToken = payload.csrf_token;
        } catch { /* keep the current in-memory token */ }
      }
      return response;
    });
  };
  window.__tradingPlatformFetchWrapped = true;
}

async function authRequest(path, options = {}) {
  const response = await fetch(`${AUTH}${path}`, {
    ...options,
    headers: { Accept: 'application/json', ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...(options.headers || {}) },
    credentials: 'include',
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || `Request failed (${response.status})`);
  return payload;
}

export default function AuthGate() {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);
  const [bootstrapRequired, setBootstrapRequired] = useState(false);
  const [mode, setMode] = useState('login');
  const [token, setToken] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [showUsers, setShowUsers] = useState(false);
  const [showSessions, setShowSessions] = useState(false);

  useEffect(() => {
    window.__tpUserRole = user?.role || '';
  }, [user]);

  useEffect(() => {
    let active = true;
    (async () => {
      try {
        await authRequest('/csrf');
        const status = await authRequest('/bootstrap/status');
        if (!active) return;
        setBootstrapRequired(Boolean(status.required));
        const query = new URLSearchParams(window.location.search);
        const verify = query.get('verify');
        const invite = query.get('invite');
        const reset = query.get('reset');
        if (verify) {
          await authRequest('/verify-email', { method: 'POST', body: JSON.stringify({ token: verify }) });
          query.delete('verify');
          window.history.replaceState({}, '', `${window.location.pathname}${query.size ? `?${query}` : ''}`);
          setMessage('Email verified. You can now sign in.');
        } else if (invite || reset) {
          setToken(invite || reset);
          setMode(invite ? 'invite' : 'reset');
        }
        try {
          const current = await authRequest('/me');
          if (active) setUser(current.user);
        } catch (sessionError) {
          if (sessionError.message !== 'Authentication required') throw sessionError;
        }
      } catch (loadError) {
        if (active) setError(loadError.message || 'Unable to connect to the authentication service.');
      } finally {
        if (active) setLoading(false);
      }
    })();
    return () => { active = false; };
  }, []);

  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setError('');
    setMessage('');
    try {
      if (mode === 'login') {
        const result = await authRequest('/login', { method: 'POST', body: JSON.stringify({ email, password }) });
        setUser(result.user);
      } else if (mode === 'bootstrap') {
        const result = await authRequest('/bootstrap', { method: 'POST', body: JSON.stringify({ email, password }) });
        setMessage(result.verification?.action_url
          ? `Initial administrator created. Open this development verification link: ${result.verification.action_url}`
          : 'Initial administrator created. Check your email to verify the account.');
      } else if (mode === 'forgot') {
        const result = await authRequest('/password/forgot', { method: 'POST', body: JSON.stringify({ email }) });
        setMessage(result.action_url ? `Development reset link: ${result.action_url}` : 'If the account exists, a password reset link has been sent.');
      } else if (mode === 'invite' || mode === 'reset') {
        const path = mode === 'invite' ? '/invitations/accept' : '/password/reset';
        await authRequest(path, { method: 'POST', body: JSON.stringify({ token, password }) });
        setMessage(mode === 'invite' ? 'Invitation accepted. You can now sign in.' : 'Password reset. You can now sign in.');
        setMode('login');
        setPassword('');
        setToken('');
      }
    } catch (submitError) {
      setError(submitError.message || 'The request could not be completed.');
    } finally {
      setBusy(false);
    }
  }

  async function signOut() {
    try { await authRequest('/logout', { method: 'POST' }); } catch (signOutError) { setError(signOutError.message); }
    setUser(null);
    setShowUsers(false);
    setShowSessions(false);
  }

  if (loading) return <div className="auth-loading">Connecting to TradingPlatformNext…</div>;
  if (!user) {
    const title = mode === 'bootstrap' ? 'Create the first administrator'
      : mode === 'forgot' ? 'Reset your password'
        : mode === 'invite' ? 'Accept your invitation'
          : mode === 'reset' ? 'Choose a new password' : 'Sign in to TradingPlatform';
    return <main className="auth-screen">
      <form className="auth-card" onSubmit={submit}>
        <div className="auth-brand">TRADING PLATFORM <span>NEXT</span></div>
        <h1>{title}</h1>
        <p className="auth-subtitle">{mode === 'bootstrap'
          ? 'Use the email configured as INITIAL_SUPER_ADMIN_EMAIL by the administrator.'
          : mode === 'invite' ? 'Set a password to activate your Viewer account.'
            : mode === 'reset' ? 'Choose a new password for your account.'
              : mode === 'forgot' ? 'Enter your account email. If it exists, a reset link will be sent.'
                : 'Use your application account. Broker login sessions remain separate.'}</p>
        {(mode === 'login' || mode === 'bootstrap' || mode === 'forgot') && <label>Email address<input type="email" autoComplete="email" value={email} onChange={(event) => setEmail(event.target.value)} required /></label>}
        {(mode === 'login' || mode === 'bootstrap' || mode === 'invite' || mode === 'reset') && <label>{mode === 'login' ? 'Password' : 'Password (minimum 12 characters)'}<input type="password" autoComplete={mode === 'login' ? 'current-password' : 'new-password'} minLength={mode === 'login' ? 1 : 12} value={password} onChange={(event) => setPassword(event.target.value)} required /></label>}
        {error && <div className="auth-error" role="alert">{error}</div>}
        {message && <div className="auth-message" role="status">{message}</div>}
        <button className="auth-primary" type="submit" disabled={busy}>{busy ? 'Please wait…' : mode === 'login' ? 'Sign in' : mode === 'bootstrap' ? 'Create administrator' : mode === 'forgot' ? 'Send reset link' : mode === 'invite' ? 'Activate account' : 'Save new password'}</button>
        <div className="auth-links">
          {mode === 'login' && <><button type="button" onClick={() => { setMode('forgot'); setError(''); setMessage(''); }}>Forgot password?</button>{bootstrapRequired && <button type="button" onClick={() => { setMode('bootstrap'); setError(''); setMessage(''); }}>Initial setup</button>}</>}
          {mode !== 'login' && <button type="button" onClick={() => { setMode('login'); setError(''); setMessage(''); }}>Back to sign in</button>}
        </div>
      </form>
    </main>;
  }

  return <div className="authenticated-app">
    <header className="auth-toolbar">
      <div><strong>TRADING PLATFORM <span>NEXT</span></strong><small>{user.email} · {user.role.replace('_', ' ')}</small></div>
      <nav>
        <button type="button" className={showSessions ? 'selected' : ''} onClick={() => { setShowSessions(!showSessions); setShowUsers(false); }}>Sessions</button>
        {user.role === 'SUPER_ADMIN' && <button type="button" className={showUsers ? 'selected' : ''} onClick={() => { setShowUsers(!showUsers); setShowSessions(false); }}>User Access</button>}
        <button type="button" onClick={signOut}>Sign out</button>
      </nav>
    </header>
    {showUsers && user.role === 'SUPER_ADMIN' ? <UserAccess /> : showSessions ? <Sessions onSignOut={signOut} /> : <App user={user} />}
  </div>;
}
