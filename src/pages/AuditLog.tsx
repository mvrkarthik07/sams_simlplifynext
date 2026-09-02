import { useEffect, useState } from 'react';
import { api } from '../lib/api';
import type { AuditLogEntry } from '../lib/types';
import { Terminal, Search } from 'lucide-react';
import { format } from 'date-fns';

export function AuditLog() {
  const [logs, setLogs] = useState<AuditLogEntry[]>([]);
  const [filter, setFilter] = useState('');

  useEffect(() => {
    api.getAuditLog().then(setLogs);
  }, []);

  const filteredLogs = logs.filter(log => 
    log.action.toLowerCase().includes(filter.toLowerCase()) || 
    log.approver?.toLowerCase().includes(filter.toLowerCase()) ||
    log.plan_hash.includes(filter) ||
    log.trace_id.includes(filter)
  );

  return (
    <div className="p-8 max-w-7xl mx-auto h-screen flex flex-col space-y-4">
      <div>
        <h2 className="text-2xl font-bold flex items-center gap-2">
          <Terminal className="w-6 h-6" /> OTEL Trace & Audit Trail
        </h2>
        <p className="text-muted-foreground mt-1">Immutable ledger of every system and human decision.</p>
      </div>

      <div className="relative">
        <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
        <input 
          type="text" 
          placeholder="Filter by hash, action, approver..." 
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          className="w-full bg-card border border-border rounded-md pl-9 pr-4 py-2 text-sm focus:outline-none focus:border-primary transition-colors font-mono"
        />
      </div>

      <div className="flex-1 bg-black rounded-lg border border-border p-4 overflow-y-auto font-mono text-sm leading-relaxed shadow-inner">
        {filteredLogs.length === 0 ? (
          <div className="text-muted-foreground opacity-50">No logs found.</div>
        ) : (
          filteredLogs.map(log => (
            <div key={log.id} className="mb-2 pb-2 border-b border-white/5 hover:bg-white/5 transition-colors -mx-4 px-4">
              <div className="flex flex-wrap gap-x-4 text-xs text-muted-foreground mb-1">
                <span>[{format(new Date(log.timestamp), 'yyyy-MM-dd HH:mm:ss')}]</span>
                <span className="text-blue-400">trace:{log.trace_id}</span>
                <span className="text-green-400">hash:{log.plan_hash.substring(0, 14)}...</span>
              </div>
              <div className="flex gap-2">
                <span className="text-warning font-bold w-32 shrink-0">{log.action.toUpperCase()}</span>
                <span className="text-gray-300 flex-1">{log.details}</span>
              </div>
              <div className="text-xs text-gray-500 mt-1">
                actor: <span className="text-gray-400">{log.approver || 'SYSTEM'}</span>
              </div>
            </div>
          ))
        )}
        <div className="text-muted-foreground mt-4 animate-pulse">_</div>
      </div>
    </div>
  );
}
