import type { Finding } from '../lib/types';

export function ProvenanceBadge({ finding }: { finding: Finding }) {
  const isCaptured = finding.source === 'captured' || finding.entitlement.raw.source === 'captured';
  const capturedAt = finding.captured_at ?? (
    typeof finding.entitlement.raw.captured_at === 'string'
      ? finding.entitlement.raw.captured_at
      : undefined
  );
  return (
    <span
      title={isCaptured && capturedAt ? `Captured at ${capturedAt}` : undefined}
      className={isCaptured
        ? 'tier-badge status-live'
        : 'tier-badge status-not-wired'}
    >
      {isCaptured ? 'Live' : 'Simulated'}
    </span>
  );
}
