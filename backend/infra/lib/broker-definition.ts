import type { IFunction } from 'aws-cdk-lib/aws-lambda';

type State = Record<string, unknown>;

const task = (functionArn: string, next: string): State => ({
  Type: 'Task',
  Resource: 'arn:aws:states:::lambda:invoke',
  Parameters: { FunctionName: functionArn, 'Payload.$': '$' },
  ResultPath: null,
  Next: next,
});

const auditTask = (functionArn: string, next: string): State => ({
  ...task(functionArn, next),
  Parameters: {
    FunctionName: functionArn,
    Payload: {
      'request.$': '$',
      'decision.$': '$.decision',
      'plan_id.$': '$.plan_id',
      'plan_hash.$': '$.plan_hash',
      'finding_id.$': '$.finding_id',
      'acted.$': '$.acted',
    },
  },
});

const choice = (variable: string, value: string, next: string): State => ({
  Variable: variable,
  StringEquals: value,
  Next: next,
});

/**
 * CDK-side rendering of backend/src/deadbolt/broker/statemachine.py.
 * Keeping the ASL parameterized by deployed ARNs preserves the Python module's
 * state-machine contract without requiring Python or backend dependencies at synth time.
 */
export function brokerDefinition(functions: {
  notify: IFunction;
  audit: IFunction;
  executor: IFunction;
  negotiator: IFunction;
  pager: IFunction;
}): Record<string, unknown> {
  const notify = functions.notify.functionArn;
  const audit = functions.audit.functionArn;
  const executor = functions.executor.functionArn;
  const negotiator = functions.negotiator.functionArn;
  const pager = functions.pager.functionArn;

  const states: Record<string, State> = {
    RouteTier: {
      Type: 'Choice',
      Choices: [
        choice('$.tier', 'T0', 'ObserveOnly'),
        choice('$.tier', 'T1', 'PrepareT1Approval'),
        choice('$.tier', 'T2', 'PrepareT2Approval'),
        choice('$.tier', 'T3', 'PageSecurityOnCall'),
      ],
      Default: 'ObserveOnly',
    },
    PrepareT1Approval: {
      Type: 'Pass', Result: 259200, ResultPath: '$.approval_timeout_seconds', Next: 'NotifyApprover',
    },
    PrepareT2Approval: {
      Type: 'Pass', Result: 86400, ResultPath: '$.approval_timeout_seconds', Next: 'NotifyApprover',
    },
    NotifyApprover: {
      Type: 'Task',
      Resource: 'arn:aws:states:::lambda:invoke.waitForTaskToken',
      HeartbeatSeconds: 300,
      TimeoutSecondsPath: '$.approval_timeout_seconds',
      Parameters: {
        FunctionName: notify,
        Payload: { 'task_token.$': '$$.Task.Token', 'request.$': '$' },
      },
      Catch: [{ ErrorEquals: ['States.Timeout'], Next: 'TimeoutByTier' }],
      Next: 'MarkDecisionDisposition',
    },
    TimeoutByTier: {
      Type: 'Choice',
      Choices: [
        choice('$.tier', 'T0', 'ProceedAfterTimeout'),
        choice('$.tier', 'T1', 'ProceedAfterTimeout'),
        choice('$.tier', 'T2', 'EscalateSecurityAdmin'),
        choice('$.tier', 'T3', 'PageSecurityOnCall'),
      ],
      Default: 'ObserveOnly',
    },
    MarkDecisionDisposition: {
      Type: 'Choice',
      Choices: [choice('$.decision', 'Approve', 'MarkApproved')],
      Default: 'MarkDeclined',
    },
    MarkApproved: { Type: 'Pass', Result: true, ResultPath: '$.acted', Next: 'RecordDecision' },
    MarkDeclined: { Type: 'Pass', Result: false, ResultPath: '$.acted', Next: 'RecordDecision' },
    RecordDecision: auditTask(audit, 'RouteDecision'),
    RouteDecision: {
      Type: 'Choice',
      Choices: [
        choice('$.decision', 'Approve', 'ExecuteApproved'),
        choice('$.decision', 'Reduce further', 'ReduceFurther'),
        choice('$.decision', 'Keep, with reason', 'KeepWithReason'),
        choice('$.decision', 'Defer 30 days', 'Succeed'),
      ],
      Default: 'DeclinedToAct',
    },
    ExecuteApproved: task(executor, 'Succeed'),
    ReduceFurther: task(negotiator, 'Succeed'),
    KeepWithReason: task(negotiator, 'Succeed'),
    DeclinedToAct: { Type: 'Pass', Result: false, ResultPath: '$.acted', Next: 'RecordDeclinedDecision' },
    RecordDeclinedDecision: auditTask(audit, 'Succeed'),
    ObserveOnly: { Type: 'Pass', Result: false, ResultPath: '$.acted', Next: 'RecordObservation' },
    RecordObservation: auditTask(audit, 'Succeed'),
    ProceedAfterTimeout: { Type: 'Pass', Result: 'Approve', ResultPath: '$.decision', Next: 'MarkApprovedTimeout' },
    MarkApprovedTimeout: { Type: 'Pass', Result: true, ResultPath: '$.acted', Next: 'RecordTimeoutProceed' },
    RecordTimeoutProceed: auditTask(audit, 'ExecuteApproved'),
    EscalateSecurityAdmin: {
      ...task(notify, 'MarkDeclinedTimeout'),
      Parameters: { FunctionName: notify, Payload: { escalation: 'security-admin', 'request.$': '$' } },
    },
    PageSecurityOnCall: { ...task(pager, 'MarkDeclinedTimeout'), Parameters: { FunctionName: pager, 'Payload.$': '$' } },
    MarkDeclinedTimeout: { Type: 'Pass', Result: false, ResultPath: '$.acted', Next: 'RecordTimeoutDecline' },
    RecordTimeoutDecline: auditTask(audit, 'Succeed'),
    Succeed: { Type: 'Succeed' },
  };

  return {
    Comment: 'Deadbolt Standard approval broker',
    StartAt: 'RouteTier',
    States: states,
  };
}
