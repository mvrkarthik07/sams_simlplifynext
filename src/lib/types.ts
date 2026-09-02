export type CredentialType = 'federated' | 'pat' | 'oauth' | 'api-key';
export type SystemType = 'aws-iam' | 'github' | 'slack' | 'notion' | 'salesforce' | 'workday';

export interface Entitlement {
  identity_id: string;
  system: SystemType;
  resource: string;
  scope: string;
  granted_at: string | null;
  last_used_at: string | null;
  credential_type: CredentialType;
  revocable: boolean;
  raw: Record<string, any>;
}

export type Tier = 'T0' | 'T1' | 'T2' | 'T3';

export interface RiskScore {
  S: number;
  D: number;
  M: number;
  B: number;
  total: number;
}

export type PipelineStage = 
  | 'Detected' 
  | 'Scored' 
  | 'Planned' 
  | 'Approval' 
  | 'Executing' 
  | 'Verified' 
  | 'Rolled back';

export type StageStatus = 'queued' | 'running' | 'passed' | 'failed' | 'blocked-on-approval' | 'rolled-back';

export interface Finding {
  finding_id: string;
  entitlement: Entitlement;
  score: RiskScore;
  tier: Tier;
  current_stage: PipelineStage;
  stage_status: StageStatus;
  evidence: {
    days_unused: number | 'never';
    role_mismatch: boolean;
    blast_radius_count: number;
  };
}

export interface PlanAction {
  seq: number;
  type: 'revoke' | 'downgrade' | 'notify';
  description: string;
  system: SystemType;
}

export interface Plan {
  plan_id: string;
  finding_id: string;
  actions: PlanAction[];
  pre_image_captured: boolean;
  hash: string;
}

export interface AuditLogEntry {
  id: string;
  timestamp: string;
  approver: string | 'System';
  action: string;
  plan_hash: string;
  trace_id: string;
  details: string;
}

export interface Metrics {
  drift_recall: number;
  false_revocation_rate: number;
  mean_time_to_revocation: string;
  approver_decision_time: string;
  reversibility: number;
  cost: number;
}
