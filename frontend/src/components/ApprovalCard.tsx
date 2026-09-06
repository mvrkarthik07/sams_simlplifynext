import type { Finding, Plan } from '../lib/types';
import { CheckCircle2, RefreshCw } from 'lucide-react';

interface ApprovalCardProps {
  finding: Finding;
  plan: Plan | null;
  actionLoading: string | null;
  onAction: (action: string) => void;
}

export function ApprovalCard({ finding, plan, actionLoading, onAction }: ApprovalCardProps) {
  const canDecide = finding.stage_status === 'blocked-on-approval' && Boolean(plan?.actions.length);
  const hasNoExecutablePlan = finding.stage_status === 'blocked-on-approval' && !plan?.actions.length;
  if (finding.current_stage !== 'Approval' && finding.current_stage !== 'Verified' && finding.current_stage !== 'Rolled back') {
    return <div className="border border-border p-4 text-sm text-muted-foreground">No operator decision is pending at this stage.</div>;
  }
  return <div className="min-w-0 w-full border border-border p-5 text-sm">
    <div className="mb-4 flex items-center gap-3 border-b border-border pb-4"><div className="brand-mark">DB</div><div><p className="font-semibold">Deadbolt broker</p><p className="text-xs text-muted-foreground">Deterministic remediation review</p></div></div>
    <p className="break-words">Deadbolt detected entitlement drift for <strong>{finding.entitlement.identity_id}</strong> in <strong>{finding.entitlement.system}</strong>.</p>
    <dl className="mt-4 min-w-0 space-y-2 border-l-2 border-warning bg-muted p-3"><div><dt className="inline font-semibold">Resource: </dt><dd className="inline break-all font-mono text-xs">{finding.entitlement.resource}</dd></div><div><dt className="inline font-semibold">Scope: </dt><dd className="inline font-mono text-xs">{finding.entitlement.scope}</dd></div><div className="border-t border-border pt-2 text-xs"><dt className="font-semibold text-muted-foreground">Evidence</dt><dd className="mt-1">Last used {finding.evidence.days_unused} days ago; blast radius reaches {finding.evidence.blast_radius_count} resources.</dd>{finding.evidence.role_mismatch && <dd>Absent from the approved role template.</dd>}</div></dl>
    <p className="mt-4 text-muted-foreground">The following deterministic remediation plan is pre-staged:</p>
    <div className="mt-2 min-w-0 break-all border border-border bg-background p-3 font-mono text-xs">{plan?.actions.map((action) => <div key={action.seq} className="break-words">{action.type}: {action.description}</div>)}<div className="mt-2 break-all text-muted-foreground">Plan hash: {plan?.hash}</div></div>
    {canDecide && <div className="mt-5 grid grid-cols-2 gap-2"><BrokerButton variant="primary" loading={actionLoading === 'Approve'} onClick={() => onAction('Approve')}>Approve</BrokerButton><BrokerButton variant="danger" loading={actionLoading === 'Reduce further'} onClick={() => onAction('Reduce further')}>Reduce further</BrokerButton><BrokerButton variant="default" loading={actionLoading === 'Keep, with reason'} onClick={() => onAction('Keep, with reason')}>Keep with reason</BrokerButton><BrokerButton variant="default" loading={actionLoading === 'Defer 30 days'} onClick={() => onAction('Defer 30 days')}>Defer 30 days</BrokerButton></div>}
    {hasNoExecutablePlan && <div className="mt-5 flex items-start gap-2 border-t border-border pt-4 text-sm text-muted-foreground"><ShieldOffIcon /><span><strong className="text-foreground">No executable remediation is available.</strong> This captured entitlement is read-only or non-revocable, so Deadbolt will not offer a destructive action for it.</span></div>}
    {!canDecide && !hasNoExecutablePlan && <div className="mt-5 flex items-center gap-2 border-t border-border pt-4 font-semibold text-accent"><CheckCircle2 size={16} /> Decision already recorded</div>}
  </div>;
}

function ShieldOffIcon() {
  return <span className="mt-0.5 inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full border border-border text-[10px]" aria-hidden="true">—</span>;
}

function BrokerButton({ children, onClick, variant, loading }: { children: React.ReactNode; onClick: () => void; variant: 'primary' | 'danger' | 'default'; loading: boolean }) {
  const style = variant === 'primary' ? 'bg-primary text-on-primary border-primary' : variant === 'danger' ? 'border-destructive text-destructive' : 'border-border text-foreground';
  return <button type="button" onClick={onClick} disabled={loading} className={`flex items-center justify-center gap-2 border px-3 py-2 text-sm font-semibold transition-opacity hover:opacity-75 disabled:opacity-50 ${style}`}>{loading && <RefreshCw size={13} className="animate-spin" />}{children}</button>;
}
