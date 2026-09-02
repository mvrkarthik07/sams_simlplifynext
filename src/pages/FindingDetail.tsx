import { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { api } from '../lib/api';
import type { Finding, Plan, PipelineStage } from '../lib/types';
import { 
  ArrowLeft, RefreshCw, CheckCircle, Clock, 
  AlertTriangle, Shield, Play, RotateCcw, Hash 
} from 'lucide-react';
import { clsx } from 'clsx';
import { twMerge } from 'tailwind-merge';
import { ApprovalCard } from '../components/ApprovalCard';

const STAGES: PipelineStage[] = ['Detected', 'Scored', 'Planned', 'Approval', 'Executing', 'Verified'];

export function FindingDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [finding, setFinding] = useState<Finding | undefined>(undefined);
  const [plan, setPlan] = useState<Plan | undefined>(undefined);
  const [isRerunning, setIsRerunning] = useState(false);
  const [previousHash, setPreviousHash] = useState<string | null>(null);
  const [isHashMatched, setIsHashMatched] = useState<boolean | null>(null);
  const [actionLoading, setActionLoading] = useState<string | null>(null);

  useEffect(() => {
    if (id) {
      api.getFinding(id).then(setFinding);
      api.getPlan(id).then(setPlan);
    }
  }, [id]);

  if (!finding) return <div className="p-8 font-mono text-muted-foreground animate-pulse">Loading finding...</div>;

  const handleRerun = async () => {
    if (!plan || !id) return;
    setIsRerunning(true);
    setPreviousHash(plan.hash);
    setIsHashMatched(null);
    
    // simulate engine rerun delay
    await new Promise(r => setTimeout(r, 1500));
    const newHash = await api.rerunDriftEngine(id);
    
    setIsRerunning(false);
    setIsHashMatched(newHash === previousHash);
  };

  const handleBrokerAction = async (action: string) => {
    if (!id) return;
    setActionLoading(action);
    const updated = await api.decideApproval(id, action, 'Demo Approver', action === 'Keep, with reason' ? 'Need it for deployment' : undefined);
    setFinding(updated);
    if (action === 'Reduce further') {
      const newPlan = await api.getPlan(id);
      if (newPlan) setPlan(newPlan);
    }
    setActionLoading(null);
  };

  const handleRollback = async () => {
    if (!id) return;
    setActionLoading('rollback');
    const updated = await api.executeRollback(id);
    setFinding(updated);
    setActionLoading(null);
  };

  const currentStageIndex = STAGES.indexOf(finding.current_stage === 'Rolled back' ? 'Verified' : finding.current_stage);

  return (
    <div className="p-8 max-w-7xl mx-auto space-y-6">
      <button 
        onClick={() => navigate(-1)}
        className="flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground transition-colors focus:outline-none focus:ring-2 focus:ring-ring rounded px-2 py-1 -ml-2"
      >
        <ArrowLeft className="w-4 h-4" /> Back to Queue
      </button>

      <div className="flex items-start justify-between">
        <div>
          <div className="flex items-center gap-3 mb-2">
            <h2 className="text-2xl font-bold font-mono">{finding.finding_id}</h2>
            <span className={twMerge(clsx("px-2 py-0.5 rounded text-xs font-bold bg-muted", 
              finding.tier === 'T3' ? 'text-destructive border border-destructive/50' : ''
            ))}>
              {finding.tier} Policy
            </span>
          </div>
          <p className="text-muted-foreground">
            Identity: <strong className="text-foreground">{finding.entitlement.identity_id}</strong>
          </p>
        </div>
        <div className="text-right">
          <div className="text-4xl font-mono font-bold leading-none">{finding.score.total.toFixed(0)}</div>
          <div className="text-xs text-muted-foreground uppercase tracking-wide mt-1">Risk Score</div>
        </div>
      </div>

      {/* Pipeline View */}
      <div className="bg-card border border-border rounded-lg p-6">
        <div className="flex items-center justify-between mb-6">
          <h3 className="font-semibold flex items-center gap-2">
            <Play className="w-4 h-4" /> Remediation Pipeline
          </h3>
          <button 
            onClick={handleRerun}
            disabled={isRerunning}
            className="flex items-center gap-2 px-3 py-1.5 bg-secondary text-xs rounded hover:bg-secondary/80 disabled:opacity-50 transition-colors focus:outline-none focus:ring-2 focus:ring-ring"
            aria-label="Re-run drift engine"
          >
            <RefreshCw className={clsx("w-3 h-3", isRerunning && "animate-spin")} /> 
            {isRerunning ? 'Engine Running...' : 'Re-run Engine'}
          </button>
        </div>

        {/* Stage Timeline (Horizontal) */}
        <div className="relative flex justify-between">
          <div className="absolute top-1/2 left-0 right-0 h-0.5 bg-border -translate-y-1/2 z-0" />
          {STAGES.map((stage, idx) => {
            const isCompleted = idx < currentStageIndex || finding.current_stage === 'Rolled back';
            const isCurrent = idx === currentStageIndex && finding.current_stage !== 'Rolled back';
            
            let statusIcon = <CheckCircle className="w-4 h-4" />;
            let statusColor = "bg-muted text-muted-foreground border-border";
            
            if (isCompleted) {
              statusColor = "bg-accent/20 border-accent text-accent";
            } else if (isCurrent) {
              if (finding.stage_status === 'blocked-on-approval') {
                statusIcon = <AlertTriangle className="w-4 h-4" />;
                statusColor = "bg-warning/20 border-warning text-warning animate-pulse";
              } else if (finding.stage_status === 'running') {
                statusIcon = <RefreshCw className="w-4 h-4 animate-spin" />;
                statusColor = "bg-primary border-primary text-on-primary";
              } else {
                statusIcon = <Clock className="w-4 h-4" />;
                statusColor = "bg-primary border-primary text-on-primary";
              }
            } else {
              statusIcon = <Clock className="w-4 h-4" />;
            }

            if (stage === 'Verified' && finding.current_stage === 'Rolled back') {
              statusIcon = <RotateCcw className="w-4 h-4" />;
              statusColor = "bg-destructive/20 border-destructive text-destructive";
            }

            return (
              <div key={stage} className="relative z-10 flex flex-col items-center gap-2 bg-card px-2">
                <div className={twMerge(clsx("w-8 h-8 rounded-full border-2 flex items-center justify-center bg-card transition-colors", statusColor))}>
                  {statusIcon}
                </div>
                <span className={clsx("text-xs font-semibold uppercase tracking-wider", isCurrent ? "text-foreground" : "text-muted-foreground")}>
                  {stage === 'Verified' && finding.current_stage === 'Rolled back' ? 'Rolled Back' : stage}
                </span>
              </div>
            );
          })}
        </div>

        {/* Plan Hash Comparison UI */}
        {plan && (
          <div className="mt-8 p-4 bg-muted/30 rounded border border-border">
            <h4 className="text-sm font-semibold mb-3 flex items-center gap-2 text-muted-foreground uppercase tracking-wide">
              <Hash className="w-4 h-4" /> Deterministic Plan Hash
            </h4>
            <div className="flex items-center gap-4">
              <div className="flex-1 font-mono text-sm bg-background p-2 rounded border border-border truncate">
                {plan.hash}
              </div>
              {isHashMatched !== null && (
                <div className="flex items-center gap-2">
                  <ArrowLeft className="w-4 h-4 text-muted-foreground" />
                  <div className="flex-1 font-mono text-sm bg-background p-2 rounded border border-border truncate opacity-50">
                    {previousHash}
                  </div>
                  <span className={clsx("text-xs font-bold px-2 py-1 rounded", isHashMatched ? "bg-accent/20 text-accent" : "bg-destructive/20 text-destructive")}>
                    {isHashMatched ? 'MATCH (DETERMINISTIC)' : 'MISMATCH'}
                  </span>
                </div>
              )}
            </div>
            {isHashMatched !== null && (
              <p className="text-xs text-muted-foreground mt-2 italic">
                The identical hash confirms the drift engine is a pure function. Same graph + same policy = exact same plan.
              </p>
            )}
          </div>
        )}
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* Evidence Panel */}
        <div className="bg-card border border-border rounded-lg p-6 space-y-4">
          <h3 className="font-semibold flex items-center gap-2 mb-4"><Shield className="w-4 h-4" /> Risk Decomposition</h3>
          
          <div className="space-y-3">
            <ScoreRow label="S — Scope Severity" value={finding.score.S.toFixed(2)} text={`Level: \${finding.entitlement.scope}`} />
            <ScoreRow label="D — Dormancy" value={finding.score.D.toFixed(2)} text={`Last used \${finding.evidence.days_unused} days ago`} />
            <ScoreRow label="M — Role Mismatch" value={finding.score.M.toFixed(2)} text={finding.evidence.role_mismatch ? "Absent from approved template" : "Present in template"} />
            <ScoreRow label="B — Blast Radius" value={finding.score.B.toFixed(2)} text={`Reaches \${finding.evidence.blast_radius_count} resources`} />
            <div className="pt-3 mt-3 border-t border-border flex justify-between items-center font-bold">
              <span>Final Risk Score</span>
              <span className="font-mono text-lg">{finding.score.total.toFixed(0)} / 100</span>
            </div>
          </div>
        </div>

        {/* Action Panel / Approval Broker */}
        <div className="bg-card border border-border rounded-lg p-6 flex flex-col">
          <h3 className="font-semibold mb-4 text-foreground flex items-center justify-between">
            Broker Interaction
            {finding.stage_status === 'blocked-on-approval' && <span className="bg-warning/20 text-warning px-2 py-0.5 rounded text-xs animate-pulse">Awaiting Decision</span>}
          </h3>
          
          <div className="flex-1 flex justify-center w-full">
            <ApprovalCard 
              finding={finding} 
              plan={plan || null} 
              actionLoading={actionLoading} 
              onAction={handleBrokerAction} 
            />
          </div>

          {finding.current_stage === 'Verified' && finding.stage_status === 'passed' && (
             <button 
              onClick={handleRollback}
              disabled={actionLoading === 'rollback'}
              className="mt-6 w-full py-2 bg-destructive/10 text-destructive border border-destructive/30 hover:bg-destructive/20 rounded font-medium flex items-center justify-center gap-2 transition-colors focus:outline-none focus:ring-2 focus:ring-destructive"
            >
              <RotateCcw className="w-4 h-4" />
              {actionLoading === 'rollback' ? 'Executing Rollback...' : 'Trigger Rollback'}
            </button>
          )}

          {finding.current_stage === 'Rolled back' && (
            <div className="mt-6 p-3 bg-accent/10 border border-accent/20 rounded text-accent flex items-center justify-center gap-2 font-medium">
              <CheckCircle className="w-4 h-4" /> Rollback Verified Successfully
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function ScoreRow({ label, value, text }: { label: string, value: string, text: string }) {
  return (
    <div className="flex items-center justify-between">
      <div>
        <div className="font-medium text-sm text-foreground">{label}</div>
        <div className="text-xs text-muted-foreground">{text}</div>
      </div>
      <div className="font-mono text-sm bg-muted px-2 py-1 rounded">{value}</div>
    </div>
  );
}
