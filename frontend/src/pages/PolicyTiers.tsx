import { ShieldAlert } from 'lucide-react';
import { clsx } from 'clsx';
import { twMerge } from 'tailwind-merge';

const TIERS = [
  { tier: 'T0', name: 'Observe', range: 'R < 30', timeout: '—', auto: 'No. Logged only.', color: 'tier-T0' },
  { tier: 'T1', name: 'Auto-downgrade', range: '30 ≤ R < 60', timeout: 'Proceeds after 72 h objection window', auto: 'Yes, reversible, subject notified at start', color: 'tier-T1' },
  { tier: 'T2', name: 'Broker', range: '60 ≤ R < 85', timeout: 'No action; escalates to security admin at 24 h', auto: 'No', color: 'tier-T2' },
  { tier: 'T3', name: 'Page', range: 'R ≥ 85', timeout: 'No action; immediate page, plan pre-staged', auto: 'No', color: 'tier-T3' },
];

export function PolicyTiers() {
  return (
    <div className="app-page max-w-5xl space-y-6">
      <div>
        <p className="eyebrow">Policy reference</p>
        <h1 className="mt-1 flex items-center gap-2 text-2xl font-semibold"><ShieldAlert className="h-6 w-6" /> Policy tiers and safety asymmetry</h1>
        <p className="muted mt-2 max-w-3xl leading-relaxed">
          The safety asymmetry is core to Deadbolt. Timeout at T1 proceeds because the action is low-severity and reversible; timeout at T2/T3 does nothing because silently cutting an unresponsive manager's report off from a production system during an incident is a worse outcome than one more day of drift.
        </p>
      </div>

      <div className="grid gap-4 mt-8">
        {TIERS.map(t => (
          <div key={t.tier} className={twMerge(clsx("border border-border bg-card p-6 flex flex-col md:flex-row gap-6 items-start md:items-center", t.color))}>
            <div className="w-48 shrink-0">
              <div className="text-2xl font-bold font-mono">{t.tier} {t.name}</div>
              <div className="text-sm opacity-80 font-mono mt-1">Score: {t.range}</div>
            </div>
            
            <div className="flex-1 grid grid-cols-1 md:grid-cols-2 gap-4 text-sm">
              <div>
                <div className="mb-1 text-xs font-semibold text-muted-foreground">Action on timeout</div>
                <div>{t.timeout}</div>
              </div>
              <div>
                <div className="mb-1 text-xs font-semibold text-muted-foreground">Executes automatically?</div>
                <div>{t.auto}</div>
              </div>
            </div>
          </div>
        ))}
      </div>
      
      <div className="mt-8 border border-border bg-card p-6 text-sm">
        <h2 className="mb-2 font-semibold">Break-glass exclusions</h2>
        <p className="muted">
          Any identity carrying <code className="bg-muted px-1 py-0.5 rounded text-foreground font-mono">oncall=true</code>, membership in a protected group, or an active incident tag is excluded from T1 auto-action entirely and routed to T2. Hard-coded, not scored.
        </p>
      </div>
    </div>
  );
}
