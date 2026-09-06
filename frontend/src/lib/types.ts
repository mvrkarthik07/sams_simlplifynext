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
  raw: Record<string, unknown>;
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
  observe_only?: boolean;
  evidence: {
    days_unused: number | 'never';
    role_mismatch: boolean;
    blast_radius_count: number;
  };
  source?: 'captured' | 'fixture';
  captured_at?: string | null;
  evaluated_at?: string;
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
  counts?: {
    planted: number;
    detected: number;
    executed: number;
    rollback_success: number;
    revocations?: number;
  };
}

export type ConnectionProvider = 'github' | 'salesforce' | 'workday';

export interface ConnectionSummary {
  provider: ConnectionProvider;
  label: string;
  status: 'connected' | 'not-configured';
  configured_at: string | null;
  last_tested_at: string | null;
  record_count: number | null;
  read_only: boolean;
}

export interface ConnectionTestResult extends ConnectionSummary {
  message: string;
  records: Array<{
    identity_id: string;
    resource: string;
    scope: string;
    credential_type: string;
    revocable: boolean;
  }>;
}

export interface ConnectionScanResult extends ConnectionTestResult {
  dashboard_updated: boolean;
}
