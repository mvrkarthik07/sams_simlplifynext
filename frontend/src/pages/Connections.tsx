import { useEffect, useState } from 'react';
import { Plug, RefreshCw, Trash2 } from 'lucide-react';
import { api } from '../lib/api';
import type { ConnectionProvider, ConnectionSummary, ConnectionTestResult } from '../lib/types';

type FormValues = Record<ConnectionProvider, Record<string, string>>;

const PROVIDERS: Array<{ id: ConnectionProvider; title: string; description: string }> = [
  { id: 'github', title: 'GitHub', description: 'Read organization members and repository permissions.' },
  { id: 'salesforce', title: 'Salesforce', description: 'Read permission-set, object, field, and login history data.' },
  { id: 'workday', title: 'Workday', description: 'Read workers and security-group assignments. Workday remains read-only.' },
];

const EMPTY_FORMS: FormValues = {
  github: { org: '', repos: '', token: '' },
  salesforce: { instance_url: '', client_id: '', username: '', private_key: '', login_url: 'https://login.salesforce.com' },
  workday: { tenant: '', base_url: '', bearer_token: '' },
};

export function Connections() {
  const [connections, setConnections] = useState<ConnectionSummary[]>([]);
  const [testResults, setTestResults] = useState<Partial<Record<ConnectionProvider, ConnectionTestResult>>>({});
  const [forms, setForms] = useState<FormValues>(EMPTY_FORMS);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    setBusy('load');
    setError(null);
    try {
      setConnections(await api.getConnections());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to load connections.');
    } finally {
      setBusy(null);
    }
  };

  useEffect(() => { void load(); }, []);

  const update = (provider: ConnectionProvider, field: string, value: string) => {
    setForms((current) => ({ ...current, [provider]: { ...current[provider], [field]: value } }));
  };

  const save = async (provider: ConnectionProvider) => {
    setBusy(`${provider}:save`);
    setMessage(null);
    setError(null);
    const values = forms[provider];
    const payload: Record<string, unknown> = provider === 'github'
      ? { ...values, repos: values.repos.split(/\r?\n|,/).map((repo) => repo.trim()).filter(Boolean) }
      : values;
    try {
      const saved = await api.saveConnection(provider, payload);
      setConnections((current) => current.map((item) => item.provider === provider ? saved : item));
      setMessage(`${saved.label} configuration saved securely.`);
      setForms((current) => ({ ...current, [provider]: { ...current[provider], token: '', private_key: '', bearer_token: '' } }));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to save connection.');
    } finally {
      setBusy(null);
    }
  };

  const test = async (provider: ConnectionProvider) => {
    setBusy(`${provider}:test`);
    setMessage(null);
    setError(null);
    try {
      const result = await api.testConnection(provider);
      setConnections((current) => current.map((item) => item.provider === provider ? result : item));
      setTestResults((current) => ({ ...current, [provider]: result }));
      setMessage(result.message);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Connection test failed.');
    } finally {
      setBusy(null);
    }
  };

  const scan = async (provider: ConnectionProvider) => {
    setBusy(`${provider}:scan`);
    setMessage(null);
    setError(null);
    try {
      const result = await api.scanConnection(provider);
      setConnections((current) => current.map((item) => item.provider === provider ? result : item));
      setTestResults((current) => ({ ...current, [provider]: result }));
      setMessage(result.message);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Dashboard scan failed.');
    } finally {
      setBusy(null);
    }
  };

  const remove = async (provider: ConnectionProvider) => {
    setBusy(`${provider}:remove`);
    setMessage(null);
    setError(null);
    try {
      const removed = await api.removeConnection(provider);
      setConnections((current) => current.map((item) => item.provider === provider ? removed : item));
      setMessage(`${removed.label} disconnected.`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to disconnect provider.');
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="app-page max-w-6xl space-y-6">
      <header className="dashboard-header">
        <div>
          <p className="eyebrow">Integrations</p>
          <h1 className="dashboard-title">Connect your platforms</h1>
          <p className="dashboard-subtitle">Credentials are sent to the authenticated backend and stored as encrypted SSM parameters. They are never returned to this browser.</p>
        </div>
        <button type="button" className="outline-button refresh-button" onClick={() => void load()} disabled={busy !== null}>
          <RefreshCw size={14} className={busy === 'load' ? 'animate-spin' : ''} /> Refresh
        </button>
      </header>

      {message && <div className="rounded border border-accent/30 bg-accent/10 p-3 text-sm text-accent" role="status">{message}</div>}
      {error && <div className="error-panel" role="alert"><p>{error}</p><button type="button" className="outline-button mt-3" onClick={() => setError(null)}>Dismiss</button></div>}

      <div className="grid gap-5 lg:grid-cols-3">
        {PROVIDERS.map((provider) => {
          const state = connections.find((item) => item.provider === provider.id);
          const values = forms[provider.id];
          const isConnected = state?.status === 'connected';
          return (
            <section key={provider.id} className="border border-border bg-card p-5">
              <div className="flex items-start justify-between gap-3 border-b border-border pb-4">
                <div className="flex items-center gap-3"><span className="brand-mark"><Plug size={16} /></span><div><h2 className="font-semibold">{provider.title}</h2><p className="text-xs text-muted-foreground">{provider.description}</p></div></div>
                <span className={isConnected ? 'text-xs font-semibold text-accent' : 'text-xs text-muted-foreground'}>{isConnected ? 'Connected' : 'Not configured'}</span>
              </div>

              {provider.id === 'github' && <>
                <Field label="Organization" value={values.org} onChange={(value) => update('github', 'org', value)} placeholder="your-org" />
                <Field label="Repositories" value={values.repos} onChange={(value) => update('github', 'repos', value)} placeholder="org/repo, one per line" multiline />
                <SecretField label="Fine-grained PAT" value={values.token} onChange={(value) => update('github', 'token', value)} placeholder="Only sent over HTTPS" />
              </>}
              {provider.id === 'salesforce' && <>
                <Field label="Instance URL" value={values.instance_url} onChange={(value) => update('salesforce', 'instance_url', value)} placeholder="https://your-domain.my.salesforce.com" />
                <Field label="Connected App client ID" value={values.client_id} onChange={(value) => update('salesforce', 'client_id', value)} />
                <Field label="Username" value={values.username} onChange={(value) => update('salesforce', 'username', value)} />
                <Field label="Login URL" value={values.login_url} onChange={(value) => update('salesforce', 'login_url', value)} />
                <SecretField label="JWT private key" value={values.private_key} onChange={(value) => update('salesforce', 'private_key', value)} multiline />
              </>}
              {provider.id === 'workday' && <>
                <Field label="Tenant" value={values.tenant} onChange={(value) => update('workday', 'tenant', value)} placeholder="acme_dpt1" />
                <Field label="API base URL" value={values.base_url} onChange={(value) => update('workday', 'base_url', value)} placeholder="https://wd2-impl-services1.workday.com" />
                <SecretField label="Bearer token" value={values.bearer_token} onChange={(value) => update('workday', 'bearer_token', value)} />
              </>}

              <div className="mt-5 grid grid-cols-2 gap-2">
                <button type="button" className="primary-button !mt-0" onClick={() => void save(provider.id)} disabled={busy !== null}>{busy === `${provider.id}:save` ? 'Saving…' : 'Save securely'}</button>
                <button type="button" className="outline-button" onClick={() => void test(provider.id)} disabled={!isConnected || busy !== null}>{busy === `${provider.id}:test` ? 'Testing…' : 'Test read-only'}</button>
              </div>
              {isConnected && <button type="button" className="mt-3 w-full border border-accent/40 bg-accent/10 px-3 py-2 text-xs font-semibold text-accent" onClick={() => void scan(provider.id)} disabled={busy !== null}>{busy === `${provider.id}:scan` ? 'Scanning…' : 'Scan into dashboard'}</button>}
              {isConnected && <button type="button" className="mt-4 flex items-center gap-2 text-xs font-semibold text-destructive" onClick={() => void remove(provider.id)} disabled={busy !== null}><Trash2 size={13} /> Disconnect</button>}
              {state?.last_tested_at && <p className="mt-3 text-xs text-muted-foreground">Last tested {state.last_tested_at} · {state.record_count ?? 0} records</p>}
              {testResults[provider.id] && <div className="mt-4 border-t border-border pt-3"><p className="text-xs font-semibold">Access preview</p><div className="mt-2 max-h-40 space-y-1 overflow-y-auto text-xs">{testResults[provider.id]?.records.map((record) => <div key={`${record.identity_id}:${record.resource}:${record.scope}`} className="border-b border-border pb-1"><span className="font-semibold">{record.identity_id}</span><span className="ml-2 font-mono text-muted-foreground">{record.resource} · {record.scope}</span></div>)}</div></div>}
              {state?.read_only && <p className="mt-3 text-xs text-muted-foreground">Read-only connector. Deadbolt will not write to Workday.</p>}
            </section>
          );
        })}
      </div>

      <p className="text-xs text-muted-foreground">Testing only reads provider metadata. Use “Scan into dashboard” when you are ready to make that verified snapshot the review dataset; scans remain read-only and never change provider access.</p>
    </div>
  );
}

function Field({ label, value, onChange, placeholder, multiline = false }: { label: string; value: string; onChange: (value: string) => void; placeholder?: string; multiline?: boolean }) {
  return <label className="mt-4 block text-xs font-semibold">{label}{multiline ? <textarea className="mt-1 min-h-16 w-full border border-border bg-background p-2 text-xs font-normal text-foreground" value={value} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} /> : <input className="mt-1 w-full border border-border bg-background p-2 text-xs font-normal text-foreground" value={value} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} />}</label>;
}

function SecretField({ label, value, onChange, placeholder, multiline = false }: { label: string; value: string; onChange: (value: string) => void; placeholder?: string; multiline?: boolean }) {
  return <label className="mt-4 block text-xs font-semibold">{label}{multiline ? <textarea className="mt-1 min-h-20 w-full border border-border bg-background p-2 text-xs font-normal text-foreground" value={value} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} autoComplete="new-password" /> : <input type="password" className="mt-1 w-full border border-border bg-background p-2 text-xs font-normal text-foreground" value={value} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} autoComplete="new-password" />}</label>;
}
