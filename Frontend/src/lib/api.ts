import type { Finding, Plan, AuditLogEntry, Metrics, RiskScore, SystemType } from './types';
import { subDays, subHours, subMinutes } from 'date-fns';

const now = new Date();

const SEED_IDENTITIES = {
  mover: 'priya.k@company.com',
  leaver: 'alex.j@company.com',
};

// Calculate Risk Score based on formula: R = 100 * (0.4S + 0.25D + 0.2M + 0.15B)
const calculateRiskScore = (S: number, D: number, M: number, B: number): RiskScore => {
  const total = 100 * (0.4 * S + 0.25 * D + 0.2 * M + 0.15 * B);
  return { S, D, M, B, total };
};

// Helper to determine tier
const getTier = (score: number) => {
  if (score < 30) return 'T0';
  if (score < 60) return 'T1';
  if (score < 85) return 'T2';
  return 'T3';
};

const generateSeedData = () => {
  const findings: Finding[] = [];
  const plans: Record<string, Plan> = {};
  const auditLogs: AuditLogEntry[] = [];

  // Mover finding 1 (Demo focus) - High Risk AWS Admin
  const moverScore1 = calculateRiskScore(0.9, 1.0, 1, 1.0); // S=admin(0.9), D=never(1), M=absent(1), B=high(1.0)
  findings.push({
    finding_id: 'FIND-001',
    entitlement: {
      identity_id: SEED_IDENTITIES.mover,
      system: 'aws-iam',
      resource: 'arn:aws:iam::account:policy/AdministratorAccess',
      scope: 'admin',
      granted_at: subDays(now, 200).toISOString(),
      last_used_at: subDays(now, 147).toISOString(),
      credential_type: 'federated',
      revocable: true,
      raw: {}
    },
    score: moverScore1,
    tier: getTier(moverScore1.total),
    current_stage: 'Approval',
    stage_status: 'blocked-on-approval',
    evidence: {
      days_unused: 147,
      role_mismatch: true,
      blast_radius_count: 31
    }
  });

  plans['FIND-001'] = {
    plan_id: 'PLAN-001',
    finding_id: 'FIND-001',
    actions: [
      { seq: 1, type: 'revoke', description: 'Detach AdministratorAccess policy from role', system: 'aws-iam' }
    ],
    pre_image_captured: true,
    hash: 'sha256:8f4c2e1a9b3d...'
  };

  auditLogs.push({
    id: 'LOG-001',
    timestamp: subHours(now, 1).toISOString(),
    approver: 'System',
    action: 'Plan Generated',
    plan_hash: 'sha256:8f4c2e1a9b3d...',
    trace_id: 'trace-49281a',
    details: 'Drift detected, T3 policy tier. Awaiting T3 manual review.'
  });

  // Leaver finding (GitHub PAT)
  const leaverScore1 = calculateRiskScore(0.5, 0.5, 1, 0.2); 
  findings.push({
    finding_id: 'FIND-002',
    entitlement: {
      identity_id: SEED_IDENTITIES.leaver,
      system: 'github',
      resource: 'org/repo-backend',
      scope: 'write',
      granted_at: subDays(now, 400).toISOString(),
      last_used_at: subDays(now, 45).toISOString(),
      credential_type: 'pat',
      revocable: true,
      raw: {}
    },
    score: leaverScore1,
    tier: getTier(leaverScore1.total),
    current_stage: 'Verified',
    stage_status: 'passed',
    evidence: {
      days_unused: 45,
      role_mismatch: true,
      blast_radius_count: 1
    }
  });

  plans['FIND-002'] = {
    plan_id: 'PLAN-002',
    finding_id: 'FIND-002',
    actions: [
      { seq: 1, type: 'revoke', description: 'Delete personal access token', system: 'github' }
    ],
    pre_image_captured: true,
    hash: 'sha256:4a1b2c3d...'
  };

  auditLogs.push({
    id: 'LOG-002',
    timestamp: subMinutes(now, 30).toISOString(),
    approver: 'System',
    action: 'System Executed',
    plan_hash: 'sha256:4a1b2c3d...',
    trace_id: 'trace-983b21',
    details: 'T1 auto-downgrade executed successfully. Rollback verified.'
  });

  // Add 18 more dummy findings to reach 20
  for(let i = 3; i <= 20; i++) {
    const s = Math.random() * 0.9 + 0.1;
    const d = Math.random();
    const sc = calculateRiskScore(s, d, 0, Math.random());
    findings.push({
      finding_id: `FIND-00\${i}`,
      entitlement: {
        identity_id: i % 2 === 0 ? SEED_IDENTITIES.mover : SEED_IDENTITIES.leaver,
        system: ['slack', 'notion', 'salesforce', 'workday'][i % 4] as SystemType,
        resource: 'internal-workspace',
        scope: s > 0.5 ? 'admin' : 'read',
        granted_at: subDays(now, 100 + i).toISOString(),
        last_used_at: subDays(now, Math.floor(d * 90)).toISOString(),
        credential_type: 'oauth',
        revocable: true,
        raw: {}
      },
      score: sc,
      tier: getTier(sc.total),
      current_stage: 'Scored',
      stage_status: 'queued',
      evidence: {
        days_unused: Math.floor(d * 90),
        role_mismatch: false,
        blast_radius_count: Math.floor(Math.random() * 5)
      }
    });
  }

  return { findings, plans, auditLogs };
};

let db = generateSeedData();

export const api = {
  getFindings: async (): Promise<Finding[]> => {
    return [...db.findings].sort((a, b) => b.score.total - a.score.total);
  },
  
  getFinding: async (id: string): Promise<Finding | undefined> => {
    return db.findings.find(f => f.finding_id === id);
  },

  getPlan: async (findingId: string): Promise<Plan | undefined> => {
    return db.plans[findingId];
  },

  getAuditLog: async (): Promise<AuditLogEntry[]> => {
    return [...db.auditLogs].sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime());
  },

  getMetrics: async (): Promise<Metrics> => {
    return {
      drift_recall: 95.0,
      false_revocation_rate: 0,
      mean_time_to_revocation: '12m 40s',
      approver_decision_time: '45s',
      reversibility: 100,
      cost: 4.52
    };
  },

  decideApproval: async (findingId: string, action: string, approver: string, reason?: string) => {
    const findingIndex = db.findings.findIndex(f => f.finding_id === findingId);
    if (findingIndex === -1) throw new Error('Finding not found');
    
    const finding = db.findings[findingIndex];
    const plan = db.plans[findingId];

    db.auditLogs.push({
      id: `LOG-\${Math.random().toString(36).substr(2, 9)}`,
      timestamp: new Date().toISOString(),
      approver,
      action,
      plan_hash: plan?.hash || 'N/A',
      trace_id: `trace-\${Math.random().toString(36).substr(2, 6)}`,
      details: reason ? `Reason: \${reason}` : 'Action approved via broker'
    });

    if (action === 'Approve') {
      db.findings[findingIndex] = {
        ...finding,
        current_stage: 'Executing',
        stage_status: 'running'
      };
    } else if (action === 'Reduce further') {
      // Simulate re-planning
      db.plans[findingId] = {
        ...plan,
        actions: [{ seq: 1, type: 'downgrade', description: 'Downgrade to Read-Only', system: finding.entitlement.system }],
        hash: `sha256:\${Math.random().toString(36).substr(2, 10)}`
      };
    } else if (action === 'Defer 30 days') {
      db.findings[findingIndex] = {
        ...finding,
        current_stage: 'Planned',
        stage_status: 'passed'
      };
    }
    
    return db.findings[findingIndex];
  },

  rerunDriftEngine: async (findingId: string) => {
    // Demo beat: identical hash on re-run
    const plan = db.plans[findingId];
    if (!plan) return null;
    
    db.auditLogs.push({
      id: `LOG-\${Math.random().toString(36).substr(2, 9)}`,
      timestamp: new Date().toISOString(),
      approver: 'System',
      action: 'Engine Re-run',
      plan_hash: plan.hash,
      trace_id: `trace-\${Math.random().toString(36).substr(2, 6)}`,
      details: 'Drift engine manually triggered. Deterministic plan matched existing hash.'
    });

    return plan.hash;
  },
  
  executeRollback: async (findingId: string) => {
    const findingIndex = db.findings.findIndex(f => f.finding_id === findingId);
    if (findingIndex === -1) throw new Error('Finding not found');
    
    db.findings[findingIndex] = {
      ...db.findings[findingIndex],
      current_stage: 'Rolled back',
      stage_status: 'rolled-back'
    };

    db.auditLogs.push({
      id: `LOG-\${Math.random().toString(36).substr(2, 9)}`,
      timestamp: new Date().toISOString(),
      approver: 'Admin',
      action: 'Rollback',
      plan_hash: db.plans[findingId]?.hash || 'N/A',
      trace_id: `trace-\${Math.random().toString(36).substr(2, 6)}`,
      details: 'Plan rolled back from pre-image successfully.'
    });

    return db.findings[findingIndex];
  }
};
