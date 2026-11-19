import React, { useCallback, useEffect, useState } from 'react';

const API = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '');

async function request(path, options = {}) {
  const response = await fetch(`${API}/api/v1/auth${path}`, {
    ...options, credentials: 'include',
    headers: { Accept: 'application/json', ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...(options.headers || {}) },
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || `Request failed (${response.status})`);
  return payload;
}

function date(value) {
  return value ? new Date(value).toLocaleString() : '—';
}

export default function Sessions({ onSignOut }) {
  const [sessions, setSessions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const result = await request('/sessions');
      setSessions(result.sessions || []);
      setError('');
    } catch (loadError) { setError(loadError.message); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { load(); }, [load]);

  async function revoke(session) {
    if (session.current) {
      if (onSignOut) await onSignOut();
      else setNotice('Use Sign out in the application toolbar to end the current session.');
      return;
    }
    try {
      await request(`/sessions/${encodeURIComponent(session.session_id)}`, { method: 'DELETE' });
      setNotice('Selected session revoked.');
      await load();
    } catch (revokeError) { setError(revokeError.message); }
  }

  return <main className="user-access-page">
    <div className="user-access-heading"><div><p className="auth-eyebrow">SECURITY</p><h1>Login Sessions</h1><p>Review active browsers and revoke sessions you no longer use.</p></div><button type="button" onClick={load}>Refresh</button></div>
    {error && <div className="auth-error" role="alert">{error}</div>}
    {notice && <div className="auth-message" role="status">{notice}</div>}
    <section className="user-access-table-wrap">
      {loading ? <p>Loading sessions…</p> : <table className="user-access-table">
        <thead><tr><th>Device / browser</th><th>IP address</th><th>Created</th><th>Last active</th><th>Expires</th><th>State</th><th>Action</th></tr></thead>
        <tbody>{sessions.map((session) => <tr key={session.session_id}>
          <td>{session.user_agent || 'Unknown browser'}</td><td>{session.ip_address || '—'}</td><td>{date(session.created_at)}</td><td>{date(session.last_seen_at)}</td><td>{date(session.expires_at)}</td>
          <td>{session.revoked ? 'Revoked' : session.current ? 'Current session' : 'Active'}</td>
          <td>{!session.revoked && <button type="button" onClick={() => revoke(session)}>{session.current ? 'Sign out' : 'Revoke'}</button>}</td>
        </tr>)}</tbody>
      </table>}
    </section>
  </main>;
}
