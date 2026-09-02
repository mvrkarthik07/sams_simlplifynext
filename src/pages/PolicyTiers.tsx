import { ShieldAlert } from 'lucide-react';
import { clsx } from 'clsx';
import { twMerge } from 'tailwind-merge';

const TIERS = [
  { tier: 'T0', name: 'Observe', range: 'R < 30', timeout: '—', auto: 'No. Logged only.', color: 'border-muted bg-muted/10 text-muted-foreground' },
  { tier: 'T1', name: 'Auto-downgrade', range: '30 ≤ R < 60', timeout: 'Proceeds after 72 h objection window', auto: 'Yes, reversible, subject notified at t=0', color: 'border-blue-900 bg-blue-900/10 text-blue-400' },
  { tier: 'T2', name: 'Broker', range: '60 ≤ R < 85', timeout: 'No action; escalates to security admin at 24 h', auto: 'No', color: 'border-warning/50 bg-warning/10 text-warning' },
  { tier: 'T3', name: 'Page', range: 'R ≥ 85', timeout: 'No action; immediate page, plan pre-staged', auto: 'No', color: 'border-destructive/50 bg-destructive/10 text-destructive' },
];

export function PolicyTiers() {
  return (
    <div className="p-8 max-w-5xl mx-auto space-y-6">
      <div>
        <h2 className="text-2xl font-bold flex items-center gap-2">
          <ShieldAlert className="w-6 h-6" /> Policy Tiers & Safety Asymmetry
        </h2>
        <p className="text-muted-foreground mt-2 max-w-3xl leading-relaxed">
          The safety asymmetry is core to Deadbolt. Timeout at T1 proceeds because the action is low-severity and reversible; timeout at T2/T3 does nothing because silently cutting an unresponsive manager's report off from a production system during an incident is a worse outcome than one more day of drift.
        </p>
      </div>

      <div className="grid gap-4 mt-8">
        {TIERS.map(t => (
          <div key={t.tier} className={twMerge(clsx("border rounded-lg p-6 flex flex-col md:flex-row gap-6 items-start md:items-center", t.color))}>
            <div className="w-48 shrink-0">
              <div className="text-2xl font-bold font-mono">{t.tier} {t.name}</div>
              <div className="text-sm opacity-80 font-mono mt-1">Score: {t.range}</div>
            </div>
            
            <div className="flex-1 grid grid-cols-1 md:grid-cols-2 gap-4 text-sm">
              <div>
                <div className="font-semibold uppercase tracking-wider text-xs mb-1 opacity-70">Action on timeout</div>
                <div>{t.timeout}</div>
              </div>
              <div>
                <div className="font-semibold uppercase tracking-wider text-xs mb-1 opacity-70">Executes automatically?</div>
                <div>{t.auto}</div>
              </div>
            </div>
          </div>
        ))}
      </div>
      
      <div className="bg-card border border-border p-6 rounded-lg mt-8 text-sm">
        <h3 className="font-bold mb-2">Break-glass exclusions</h3>
        <p className="text-muted-foreground">
          Any identity carrying <code className="bg-muted px-1 py-0.5 rounded text-foreground font-mono">oncall=true</code>, membership in a protected group, or an active incident tag is excluded from T1 auto-action entirely and routed to T2. Hard-coded, not scored.
        </p>
      </div>
    </div>
  );
}
