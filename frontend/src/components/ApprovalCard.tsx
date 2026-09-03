import type { Finding, Plan } from '../lib/types';
import { RefreshCw, CheckCircle2 } from 'lucide-react';
import { clsx } from 'clsx';
import { twMerge } from 'tailwind-merge';

interface ApprovalCardProps {
  finding: Finding;
  plan: Plan | null;
  actionLoading: string | null;
  onAction: (action: string) => void;
}

export function ApprovalCard({ finding, plan, actionLoading, onAction }: ApprovalCardProps) {
  if (finding.current_stage !== 'Approval' && finding.current_stage !== 'Verified' && finding.current_stage !== 'Rolled back') {
    return (
      <div className="bg-[#1a1d21] border border-[#383a3f] rounded-lg p-4 font-sans max-w-lg">
        <p className="text-gray-400 text-sm">No pending approval broker interaction for this stage.</p>
      </div>
    );
  }

  return (
    <div className="bg-[#1a1d21] text-[#d1d2d3] border border-[#383a3f] rounded-lg p-5 font-sans shadow-xl max-w-xl">
      {/* Slack Header */}
      <div className="flex items-center gap-3 mb-4">
        <div className="w-8 h-8 rounded bg-primary flex items-center justify-center font-bold text-on-primary">DB</div>
        <div>
          <div className="font-bold text-white text-sm">Deadbolt Negotiator <span className="bg-blue-600 text-xs px-1 rounded ml-1 text-white">APP</span></div>
          <div className="text-xs text-gray-400">8:14 AM</div>
        </div>
      </div>

      <div className="space-y-4 text-sm">
        <p>
          <strong className="text-white">Deadbolt</strong> detected entitlement drift for <strong className="text-white">{finding.entitlement.identity_id}</strong> on <strong className="text-white">{finding.entitlement.system}</strong>.
        </p>

        <div className="bg-[#222529] p-3 rounded border-l-4 border-l-warning space-y-2">
          <div><span className="font-bold text-white">Resource:</span> <code className="text-[#e8912d] bg-[#1a1d21] px-1 py-0.5 rounded text-xs">{finding.entitlement.resource}</code></div>
          <div><span className="font-bold text-white">Scope:</span> <code className="text-[#e8912d] bg-[#1a1d21] px-1 py-0.5 rounded text-xs">{finding.entitlement.scope}</code></div>
          
          <div className="pt-2 border-t border-[#383a3f] mt-2 text-xs">
            <span className="font-bold text-gray-400 uppercase tracking-wide">Evidence</span>
            <ul className="list-disc pl-4 mt-1 space-y-1">
              <li>Last used {finding.evidence.days_unused} days ago</li>
              {finding.evidence.role_mismatch && <li>Absent from ratified role template</li>}
              <li>Blast radius reaches {finding.evidence.blast_radius_count} resources</li>
            </ul>
          </div>
        </div>

        <p className="text-gray-300">
          The following deterministic remediation plan is pre-staged:
        </p>

        <div className="bg-[#000000] p-3 rounded border border-[#383a3f] font-mono text-xs text-green-400 space-y-1">
          {plan?.actions.map(act => (
            <div key={act.seq}>
              {'>'} {act.type.toUpperCase()}: {act.description}
            </div>
          ))}
          <div className="text-gray-500 mt-2"># Plan Hash: {plan?.hash.substring(0, 16)}...</div>
        </div>

        {finding.stage_status === 'blocked-on-approval' ? (
          <div className="pt-2">
            <div className="font-bold text-white mb-2">Required Action</div>
            <div className="grid grid-cols-2 gap-2">
              <SlackButton variant="primary" loading={actionLoading === 'Approve'} onClick={() => onAction('Approve')}>
                Approve
              </SlackButton>
              <SlackButton variant="danger" loading={actionLoading === 'Reduce further'} onClick={() => onAction('Reduce further')}>
                Reduce further
              </SlackButton>
              <SlackButton variant="default" loading={actionLoading === 'Keep, with reason'} onClick={() => onAction('Keep, with reason')}>
                Keep (w/ reason)
              </SlackButton>
              <SlackButton variant="default" loading={actionLoading === 'Defer 30 days'} onClick={() => onAction('Defer 30 days')}>
                Defer 30 days
              </SlackButton>
            </div>
          </div>
        ) : (
          <div className="pt-2 mt-4 border-t border-[#383a3f] text-green-500 font-bold flex items-center gap-2">
            <CheckCircle2 className="w-4 h-4" /> Action submitted.
          </div>
        )}
      </div>
    </div>
  );
}

function SlackButton({ children, onClick, variant, loading }: { children: React.ReactNode, onClick: () => void, variant: 'primary' | 'danger' | 'default', loading: boolean }) {
  const styles = {
    primary: "bg-[#007a5a] hover:bg-[#148567] text-white border-transparent",
    danger: "bg-[#e01e5a] hover:bg-[#c11a4e] text-white border-transparent",
    default: "bg-[#2c2d30] hover:bg-[#35373b] text-white border-[#56585d]"
  };

  return (
    <button
      onClick={onClick}
      disabled={loading}
      className={twMerge(clsx(
        "py-1.5 px-3 text-sm font-bold rounded border transition-colors focus:outline-none flex justify-center items-center gap-2 disabled:opacity-50",
        styles[variant]
      ))}
    >
      {loading && <RefreshCw className="w-3 h-3 animate-spin" />}
      {children}
    </button>
  );
}
