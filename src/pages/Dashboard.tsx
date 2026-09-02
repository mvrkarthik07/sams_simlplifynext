import { useEffect, useState } from 'react';
import { api } from '../lib/api';
import type { Finding, Metrics, Tier } from '../lib/types';
import { useNavigate } from 'react-router-dom';
import { ShieldAlert, CheckCircle2, Clock, DollarSign, Activity, History } from 'lucide-react';
import { clsx } from 'clsx';
import { twMerge } from 'tailwind-merge';

export function Dashboard() {
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [findings, setFindings] = useState<Finding[]>([]);
  const navigate = useNavigate();

  useEffect(() => {
    api.getMetrics().then(setMetrics);
    api.getFindings().then(setFindings);
  }, []);

  const TierBadge = ({ tier }: { tier: Tier }) => {
    const colors = {
      T0: "bg-muted text-muted-foreground",
      T1: "bg-blue-900/30 text-blue-400 border border-blue-900",
      T2: "bg-warning/20 text-warning border border-warning/50",
      T3: "bg-destructive/20 text-destructive border border-destructive/50"
    };
    return (
      <span className={twMerge(clsx("px-2 py-0.5 rounded text-xs font-bold", colors[tier]))}>
        {tier}
      </span>
    );
  };

  return (
    <div className="p-8 max-w-7xl mx-auto space-y-8">
      <div>
        <h2 className="text-2xl font-bold mb-2">Platform Overview</h2>
        <p className="text-muted-foreground">Governance metrics and active entitlement drift queue.</p>
      </div>

      {/* KPI Grid */}
      {metrics && (
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
          <KpiCard title="Drift Recall" value={`\${metrics.drift_recall}%`} icon={ShieldAlert} />
          <KpiCard title="False Revoke" value={`\${metrics.false_revocation_rate}%`} icon={CheckCircle2} />
          <KpiCard title="MTTR" value={metrics.mean_time_to_revocation} icon={Clock} />
          <KpiCard title="Decision Time" value={metrics.approver_decision_time} icon={Activity} />
          <KpiCard title="Reversibility" value={`\${metrics.reversibility}%`} icon={History} />
          <KpiCard title="Sandbox Cost" value={`$\${metrics.cost}`} icon={DollarSign} />
        </div>
      )}

      {/* Drift Queue */}
      <div className="bg-card border border-border rounded-lg overflow-hidden">
        <div className="p-4 border-b border-border bg-muted/30">
          <h3 className="font-semibold">Ranked Drift Findings</h3>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="bg-muted text-muted-foreground uppercase text-xs">
              <tr>
                <th className="px-4 py-3 font-semibold">Risk Score</th>
                <th className="px-4 py-3 font-semibold">Identity</th>
                <th className="px-4 py-3 font-semibold">System & Entitlement</th>
                <th className="px-4 py-3 font-semibold">Stage</th>
                <th className="px-4 py-3 font-semibold text-right">Action</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {findings.map(finding => (
                <tr key={finding.finding_id} className="hover:bg-muted/50 transition-colors">
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-lg">{finding.score.total.toFixed(0)}</span>
                      <TierBadge tier={finding.tier} />
                    </div>
                  </td>
                  <td className="px-4 py-3 font-medium">{finding.entitlement.identity_id}</td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <span className="uppercase text-xs font-bold text-muted-foreground tracking-wider">{finding.entitlement.system}</span>
                      <span className="font-mono bg-muted px-1.5 py-0.5 rounded text-xs">{finding.entitlement.scope}</span>
                    </div>
                    <div className="text-muted-foreground text-xs truncate max-w-xs mt-1" title={finding.entitlement.resource}>
                      {finding.entitlement.resource}
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <span className="flex items-center gap-1.5 text-xs font-semibold">
                      <span className={clsx(
                        "w-2 h-2 rounded-full",
                        finding.stage_status === 'failed' || finding.stage_status === 'blocked-on-approval' ? 'bg-warning' : 'bg-accent'
                      )} />
                      {finding.current_stage}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-right">
                    <button 
                      onClick={() => navigate(`/finding/\${finding.finding_id}`)}
                      className="px-3 py-1.5 bg-secondary text-foreground text-xs font-medium rounded hover:bg-secondary/80 focus:ring-2 focus:ring-ring focus:outline-none transition-colors"
                    >
                      Investigate
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function KpiCard({ title, value, icon: Icon }: { title: string, value: string | number, icon: any }) {
  return (
    <div className="bg-card border border-border p-4 rounded-lg flex flex-col justify-between">
      <div className="flex items-center justify-between mb-4 text-muted-foreground">
        <span className="text-xs font-bold uppercase tracking-wider">{title}</span>
        <Icon className="w-4 h-4 opacity-70" />
      </div>
      <div className="text-2xl font-mono">{value}</div>
    </div>
  );
}
