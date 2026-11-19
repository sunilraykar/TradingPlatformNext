import React, { useCallback, useEffect, useState } from 'react';

const API = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '');

async function request(path, options = {}) {
  const response = await fetch(`${API}/api/v1/auth${path}`, {
    ...options,
    credentials: 'include',
    headers: { Accept: 'application/json', ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...(options.headers || {}) },
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || `Request failed (${response.status})`);
  return payload;
}

export default function UserAccess() {
  const [users, setUsers] = useState([]);
  const [email, setEmail] = useState('');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  const loadUsers = useCallback(async () => {
    setLoading(true);
    try {
      const result = await request('/users');
      setUsers(result.users || []);
      setError('');
    } catch (loadError) {
      setError(loadError.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadUsers(); }, [loadUsers]);

  async function invite(event) {
    event.preventDefault();
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const result = await request('/users/invitations', { method: 'POST', body: JSON.stringify({ email }) });
      setNotice(result.action_url ? `Development invitation link: ${result.action_url}` : `Invitation sent to ${email}.`);
      setEmail('');
      await loadUsers();
    } catch (inviteError) {
      setError(inviteError.message);
    } finally {
      setBusy(false);
    }
  }

  async function changeStatus(user, status) {
    setError('');
    setNotice('');
    try {
      await request(`/users/${encodeURIComponent(user.user_id)}/status`, {
        method: 'PATCH',
        body: JSON.stringify({ status }),
      });
      setNotice(`${user.email} is now ${status.toLowerCase()}.`);
      await loadUsers();
    } catch (changeError) {
      setError(changeError.message);
    }
  }

  return <main className="user-access-page">
    <div className="user-access-heading"><div><p className="auth-eyebrow">ADMINISTRATION</p><h1>User Access</h1><p>Invite Viewer accounts and control application access.</p></div><button type="button" onClick={loadUsers}>Refresh</button></div>
    <form className="user-invite-form" onSubmit={invite}>
      <label>Invite a Viewer<input type="email" value={email} onChange={(event) => setEmail(event.target.value)} placeholder="name@example.com" required /></label>
      <button type="submit" disabled={busy}>{busy ? 'Sending…' : 'Send invitation'}</button>
    </form>
    {error && <div className="auth-error" role="alert">{error}</div>}
    {notice && <div className="auth-message" role="status">{notice}</div>}
    <section className="user-access-table-wrap">
      <h2>Application users</h2>
      {loading ? <p>Loading users…</p> : <table className="user-access-table">
        <thead><tr><th>Email</th><th>Role</th><th>Status</th><th>Verified</th><th>Action</th></tr></thead>
        <tbody>{users.map((user) => <tr key={user.user_id}>
          <td>{user.email}</td><td>{user.role.replace('_', ' ')}</td><td><span className={`user-status ${user.status.toLowerCase()}`}>{user.status.replace('_', ' ')}</span></td>
          <td>{user.email_verified_at ? new Date(user.email_verified_at).toLocaleDateString() : '—'}</td>
          <td>{user.role === 'VIEWER' && <button type="button" onClick={() => changeStatus(user, user.status === 'DISABLED' ? 'ACTIVE' : 'DISABLED')}>{user.status === 'DISABLED' ? 'Enable' : 'Disable'}</button>}</td>
        </tr>)}</tbody>
      </table>}
    </section>
  </main>;
}
