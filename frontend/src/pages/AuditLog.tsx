import { useEffect, useState } from 'react';
import { api } from '../lib/api';
import type { AuditLogEntry } from '../lib/types';
import { Search } from 'lucide-react';
import { format } from 'date-fns';

export function AuditLog() {
  const [logs, setLogs] = useState<AuditLogEntry[]>([]);
  const [filter, setFilter] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<{ code?: string; message: string } | null>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      setLogs(await api.getAuditLog());
    } catch (reason) {
      setError({
        code: typeof reason === 'object' && reason !== null && 'code' in reason
          ? String(reason.code)
          : undefined,
        message: reason instanceof Error ? reason.message : 'Unable to load the audit trail.',
      });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void load(); }, []);

  const filteredLogs = logs.filter(log => 
    log.action.toLowerCase().includes(filter.toLowerCase()) || 
    log.approver?.toLowerCase().includes(filter.toLowerCase()) ||
    log.plan_hash.includes(filter) ||
    log.trace_id.includes(filter)
  );

  return (
    <div className="app-page flex min-h-screen flex-col space-y-5">
      <div>
        <p className="eyebrow">Accountability</p>
        <h1 className="mt-1 text-2xl font-semibold">Audit trail</h1>
        <p className="muted mt-2 text-sm">Immutable ledger of system and operator decisions.</p>
      </div>

      <div className="relative">
        <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
        <input 
          type="text" 
          placeholder="Filter by hash, action, approver..." 
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          className="w-full border border-border bg-card py-2 pl-9 pr-4 font-mono text-sm focus:outline-none focus:border-primary"
        />
      </div>

      <div className="data-table flex-1 overflow-y-auto bg-card p-4 font-mono text-sm leading-relaxed">
        {loading ? (
          <div className="animate-pulse text-muted-foreground">Loading audit entries…</div>
        ) : error ? (
          <div className="space-y-3 text-destructive">
            <div>{error.code ? `${error.code}: ` : ''}{error.message}</div>
            <button type="button" onClick={() => void load()} className="rounded bg-secondary px-3 py-1.5 text-xs font-semibold text-foreground">
              Retry
            </button>
          </div>
        ) : filteredLogs.length === 0 ? (
          <div className="text-muted-foreground">No logs found. Run a scan or choose a broader filter.</div>
        ) : (
          filteredLogs.map(log => (
            <div key={log.id} className="mb-2 border-b border-border pb-2 hover:bg-muted">
              <div className="flex flex-wrap gap-x-4 text-xs text-muted-foreground mb-1">
                <span>[{format(new Date(log.timestamp), 'yyyy-MM-dd HH:mm:ss')}]</span>
                <span className="text-primary">trace:{log.trace_id}</span>
                <span className="text-accent">hash:{log.plan_hash.substring(0, 14)}...</span>
              </div>
              <div className="flex gap-2">
                <span className="text-warning w-32 shrink-0 font-semibold">{log.action}</span>
                <span className="flex-1 text-foreground">{log.details}</span>
              </div>
              <div className="mt-1 text-xs text-muted-foreground">
                Actor: <span className="text-foreground">{log.approver || 'System'}</span>
              </div>
            </div>
          ))
        )}
        <div className="mt-4 text-muted-foreground" aria-hidden="true">End of audit trail</div>
      </div>
    </div>
  );
}
