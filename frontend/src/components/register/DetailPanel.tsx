import { useEffect, useRef, useState } from 'react';
import type { KeyboardEvent } from 'react';
import X from 'lucide-react/dist/esm/icons/x.mjs';
import { api } from '../../lib/api';
import {
  absoluteTime,
  captureTime,
  entitlementLabel,
  errorMessage,
  findingSource,
  riskBand,
  stageLabel,
  stagePosition,
  TIER_NAMES,
} from '../../lib/register';
import type { Finding, Plan } from '../../lib/types';

interface Props {
  finding: Finding;
  onClose: () => void;
  onDecision: (
    finding: Finding,
    action: string,
    label: string,
    reason?: string,
  ) => void;
  busy: boolean;
  pendingLabel: string | null;
  onUndo: () => void;
  feedback: { text: string; error?: boolean } | null;
  onDismiss: () => void;
}
export default function DetailPanel({
  finding,
  onClose,
  onDecision,
  busy,
  pendingLabel,
  onUndo,
  feedback,
  onDismiss,
}: Props) {
  const dialog = useRef<HTMLDialogElement>(null);
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [phase, setPhase] = useState('entering');
  const [plan, setPlan] = useState<Plan | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  const [reason, setReason] = useState('');
  const trapFocus = (event: KeyboardEvent<HTMLDialogElement>) => {
    if (event.key !== 'Tab') return;
    const targets = Array.from(
      event.currentTarget.querySelectorAll<HTMLElement>(
        'button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
      ),
    ).filter((element) => element.getClientRects().length > 0);
    if (!targets.length) return;
    event.preventDefault();
    const index = targets.indexOf(document.activeElement as HTMLElement);
    targets[
      (index + (event.shiftKey ? targets.length - 1 : 1)) % targets.length
    ].focus();
  };
  const close = () => {
    if (closeTimer.current) return;
    setPhase('closing');
    closeTimer.current = setTimeout(
      () => {
        dialog.current?.close();
        onClose();
      },
      matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 140,
    );
  };
  useEffect(() => {
    const node = dialog.current;
    node?.showModal();
    const frame = requestAnimationFrame(() => setPhase('open'));
    return () => {
      cancelAnimationFrame(frame);
      if (closeTimer.current) clearTimeout(closeTimer.current);
      node?.close();
    };
  }, []);
  useEffect(() => {
    let canceled = false;
    api
      .getPlan(finding.finding_id)
      .then((result) => {
        if (!canceled) {
          setPlan(result ?? null);
          setLoading(false);
        }
      })
      .catch((cause) => {
        if (!canceled) {
          setError(errorMessage(cause));
          setLoading(false);
        }
      });
    return () => {
      canceled = true;
    };
  }, [finding.finding_id, retry]);
  const band = riskBand(finding.score.total);
  const time = captureTime(finding);
  const executable =
    !finding.observe_only &&
    finding.entitlement.revocable &&
    !!plan?.actions.some((action) => action.type !== 'notify');
  const actions = [
    { wire: 'Approve', label: 'Approve plan' },
    { wire: 'Reduce further', label: 'Reduce further' },
    { wire: 'Defer 30 days', label: 'Defer 30 days' },
    { wire: 'Keep, with reason', label: 'Keep access' },
  ];
  return (
    <dialog
      ref={dialog}
      onKeyDown={trapFocus}
      role="dialog"
      aria-modal="true"
      aria-labelledby="detail-finding-id"
      className={`register-dialog panel-${phase}`}
      onCancel={(event) => {
        event.preventDefault();
        close();
      }}
      onClick={(event) => {
        if (event.target === event.currentTarget) close();
      }}
    >
      <div className="detail-sheet">
        <header className="detail-header">
          <h2 id="detail-finding-id">
            <code>{finding.finding_id}</code>
          </h2>
          <button
            autoFocus
            className="register-icon-button"
            onClick={close}
            aria-label="Close finding details"
          >
            <X size={16} aria-hidden="true" />
          </button>
        </header>
        <div className="detail-body">
          <section className="detail-overview">
            <h3>{finding.entitlement.identity_id}</h3>
            <p>{entitlementLabel(finding)}</p>
            <div className={`detail-risk risk-${band.key}`}>
              <span className="risk-rail" aria-hidden="true">
                {Array.from({ length: band.ticks }, (_, index) => (
                  <i key={index} />
                ))}
              </span>
              <strong>{Math.round(finding.score.total)}</strong>
              <span>{band.key}</span>
              <span className={`tier-badge tier-${finding.tier}`}>
                {finding.tier} {TIER_NAMES[finding.tier]}
              </span>
            </div>
            <p>
              {stageLabel(finding)}{' '}
              <span className="pipeline-position">
                {stagePosition(finding)}/5
              </span>
            </p>
          </section>
          <section>
            <h3>Evidence</h3>
            <dl className="evidence-list">
              <div>
                <dt>Last used</dt>
                <dd>
                  {finding.evidence.days_unused === 'never'
                    ? 'No usage recorded'
                    : `${finding.evidence.days_unused} days ago`}
                </dd>
              </div>
              <div>
                <dt>Role mismatch</dt>
                <dd>
                  {finding.evidence.role_mismatch
                    ? 'Outside approved role'
                    : 'None recorded'}
                </dd>
              </div>
              <div>
                <dt>Reachable resources</dt>
                <dd>{finding.evidence.blast_radius_count}</dd>
              </div>
              <div>
                <dt>Captured</dt>
                <dd>{time ? absoluteTime(time) : 'Time unavailable'}</dd>
              </div>
              <div>
                <dt>Source</dt>
                <dd>
                  {findingSource(finding) === 'fixture'
                    ? 'Fixture'
                    : 'Live capture'}
                </dd>
              </div>
            </dl>
            {finding.observe_only ? (
              <p className="detail-note">
                Observe only. This entitlement cannot be revoked automatically.
              </p>
            ) : null}
          </section>
          <section>
            <h3>Proposed plan</h3>
            {loading ? (
              <div
                className="skeleton plan-skeleton"
                aria-label="Loading proposed plan"
              />
            ) : error ? (
              <div className="detail-error">
                <p>{error}</p>
                <button
                  className="register-button"
                  onClick={() => {
                    setLoading(true);
                    setError(null);
                    setRetry((value) => value + 1);
                  }}
                >
                  Retry plan
                </button>
              </div>
            ) : plan ? (
              <>
                <ol className="plan-actions">
                  {plan.actions.length ? (
                    plan.actions.map((action) => (
                      <li key={action.seq}>{action.description}</li>
                    ))
                  ) : (
                    <li>
                      Observe this entitlement. No executable action is staged.
                    </li>
                  )}
                </ol>
                <p className="detail-note">
                  {plan.pre_image_captured
                    ? 'Pre-image captured for rollback.'
                    : 'No pre-image captured yet.'}
                </p>
                <dl className="identifier-list">
                  <dt>Plan ID</dt>
                  <dd>
                    <code>{plan.plan_id}</code>
                  </dd>
                  <dt>Plan hash</dt>
                  <dd>
                    <code>{plan.hash}</code>
                  </dd>
                </dl>
              </>
            ) : (
              <p className="detail-note">
                No plan is available. Refresh the queue to check for a newer
                finding.
              </p>
            )}
          </section>
          <section>
            <h3>Identifiers</h3>
            <dl className="identifier-list">
              <dt>Resource</dt>
              <dd>
                <code>{finding.entitlement.resource}</code>
              </dd>
              <dt>Credential type</dt>
              <dd>
                <span>
                  {
                    {
                      federated: 'Federated sign-in',
                      pat: 'Personal access token',
                      oauth: 'OAuth grant',
                      'api-key': 'API key',
                    }[finding.entitlement.credential_type]
                  }
                </span>
              </dd>
              <dt>Scope</dt>
              <dd>{finding.entitlement.scope}</dd>
            </dl>
          </section>
          <section className="detail-decisions">
            <h3>Review decision</h3>
            <p className="detail-note">
              Decisions here use the rehearsal workflow. They do not change
              access in the provider.
            </p>
            <label className="reason-label" htmlFor="decision-reason">
              Reason for keeping access
            </label>
            <textarea
              id="decision-reason"
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              placeholder="Explain why this access is still needed"
              maxLength={2000}
              disabled={busy}
            />
            <div className="decision-buttons">
              {actions.map((action) => (
                <button
                  key={action.wire}
                  className="register-button"
                  disabled={
                    loading ||
                    busy ||
                    !plan ||
                    (action.wire !== 'Defer 30 days' && !executable) ||
                    (action.wire === 'Keep, with reason' && !reason.trim())
                  }
                  onClick={() =>
                    onDecision(
                      finding,
                      action.wire,
                      action.label,
                      reason.trim(),
                    )
                  }
                >
                  {action.label}
                </button>
              ))}
              {plan?.pre_image_captured &&
              finding.current_stage === 'Verified' ? (
                <button
                  className="register-button"
                  disabled={busy}
                  onClick={() =>
                    onDecision(finding, 'rollback', 'Restore access')
                  }
                >
                  Restore access
                </button>
              ) : null}
            </div>
            {pendingLabel ? (
              <div className="pending-decision">
                <p>{pendingLabel} recorded. Undo is available for 8 seconds.</p>
                <button className="register-button" onClick={onUndo}>
                  Undo
                </button>
              </div>
            ) : feedback ? (
              <div
                className={`detail-feedback${feedback.error ? ' detail-error' : ''}`}
              >
                <p>{feedback.text}</p>
                <button className="register-button" onClick={onDismiss}>
                  Dismiss
                </button>
              </div>
            ) : null}
          </section>
        </div>
      </div>
    </dialog>
  );
}
