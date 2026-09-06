import type { Finding, Metrics, PipelineStage, Tier } from './types';

export const RISK_BANDS = [
  { ceiling: 30, key: 'low', ticks: 1 },
  { ceiling: 60, key: 'elevated', ticks: 2 },
  { ceiling: 85, key: 'high', ticks: 3 },
  { ceiling: Infinity, key: 'critical', ticks: 4 },
] as const;
export const riskBand = (score: number) =>
  RISK_BANDS.find((band) => score < band.ceiling) ?? RISK_BANDS[3];
export const TIERS: Tier[] = ['T0', 'T1', 'T2', 'T3'];
export const TIER_NAMES = {
  T0: 'Observe',
  T1: 'Auto-downgrade',
  T2: 'Broker',
  T3: 'Page',
};
export type SourceFilter = 'all' | 'fixture' | 'captured';
export type TierFilter = 'all' | Tier;
export type SortKey = 'risk' | 'tier' | 'identity' | 'stage';
export interface Sort {
  key: SortKey;
  descending: boolean;
}
export const SOURCE_NAMES = {
  all: 'All sources',
  fixture: 'Fixture',
  captured: 'Live',
};
const configuredStaleMinutes = Number(
  import.meta.env.VITE_CAPTURE_STALE_MINUTES ?? 15,
);
export const STALE_MS =
  (Number.isFinite(configuredStaleMinutes) && configuredStaleMinutes > 0
    ? configuredStaleMinutes
    : 15) * 60_000;
const POSITIONS: Record<PipelineStage, number> = {
  Detected: 1,
  Scored: 1,
  Planned: 2,
  Approval: 3,
  Executing: 4,
  Verified: 5,
  'Rolled back': 5,
};
export const stagePosition = (finding: Finding) =>
  POSITIONS[finding.current_stage];
export function stageLabel(finding: Finding): string {
  const status =
    finding.stage_status === 'blocked-on-approval'
      ? 'blocked'
      : finding.stage_status === 'rolled-back'
        ? 'restored'
        : finding.stage_status;
  return `${finding.current_stage} · ${status}`;
}
export const findingSource = (finding: Finding): 'captured' | 'fixture' =>
  finding.source === 'captured' || finding.entitlement.raw.source === 'captured'
    ? 'captured'
    : 'fixture';
export function captureTime(finding: Finding): number | null {
  const raw =
    finding.captured_at ??
    finding.entitlement.raw.captured_at ??
    finding.evaluated_at;
  const time = typeof raw === 'string' ? Date.parse(raw) : NaN;
  return Number.isFinite(time) ? time : null;
}
export function entitlementLabel(finding: Finding): string {
  const { resource, scope } = finding.entitlement;
  const credential = resource
    .match(/^[a-z0-9-]+:(pat|oauth|token|api-key):/i)?.[1]
    ?.toLowerCase();
  if (credential)
    return (
      {
        pat: 'Personal access token',
        oauth: 'OAuth grant',
        token: 'Access token',
        'api-key': 'API key',
      }[credential] ?? 'Credential'
    );
  // Provider URNs belong in evidence; the queue uses their final resource name.
  const name = resource.startsWith('arn:')
    ? (resource.split('/').at(-1) ?? resource)
    : resource;
  return `${scope} on ${name}`;
}
export const absoluteTime = (time: number) =>
  new Intl.DateTimeFormat('en-SG', {
    timeZone: 'Asia/Singapore',
    dateStyle: 'medium',
    timeStyle: 'long',
  }).format(time);
export const shortTime = (time: number) =>
  new Intl.DateTimeFormat('en-GB', {
    timeZone: 'Asia/Singapore',
    hour: '2-digit',
    minute: '2-digit',
  }).format(time);
export function relativeTime(time: number, now: number): string {
  const minutes = Math.max(0, Math.floor((now - time) / 60_000));
  if (minutes < 1) return 'captured just now';
  if (minutes < 60) return `captured ${minutes} min ago`;
  if (minutes < 1440) return `captured ${Math.floor(minutes / 60)} hr ago`;
  return `captured ${Math.floor(minutes / 1440)} days ago`;
}
export interface FindingGroup {
  identity: string;
  findings: Finding[];
}
/** One traversal of source records; counts, metadata, lookup and matching buckets share it.
 * Ordering then visits only the matching buckets. No derived-state effect is needed. */
export function deriveRegister(
  findings: Finding[],
  tier: TierFilter,
  source: SourceFilter,
  search: string,
  sort: Sort,
) {
  const tierCounts = { all: 0, T0: 0, T1: 0, T2: 0, T3: 0 };
  const sourceCounts = { all: 0, fixture: 0, captured: 0 };
  const grouped = new Map<string, FindingGroup>();
  const byId = new Map<string, Finding>();
  const systems = new Set<string>();
  let latestCapture: number | null = null;
  let captured = 0;
  let count = 0;
  const query = search.trim().toLowerCase();
  for (const finding of findings) {
    byId.set(finding.finding_id, finding);
    systems.add(finding.entitlement.system);
    const time = captureTime(finding);
    if (time !== null) latestCapture = Math.max(latestCapture ?? time, time);
    const sourceKey = findingSource(finding);
    if (sourceKey === 'captured') captured++;
    const matchesSearch =
      !query ||
      `${finding.finding_id} ${finding.entitlement.identity_id} ${entitlementLabel(finding)} ${finding.entitlement.system}`
        .toLowerCase()
        .includes(query);
    const matchesTier = tier === 'all' || finding.tier === tier;
    const matchesSource = source === 'all' || sourceKey === source;
    if (matchesSearch && matchesSource) {
      tierCounts.all++;
      tierCounts[finding.tier]++;
    }
    if (matchesSearch && matchesTier) {
      sourceCounts.all++;
      sourceCounts[sourceKey]++;
    }
    if (!matchesSearch || !matchesTier || !matchesSource) continue;
    count++;
    const identity = finding.entitlement.identity_id;
    const group = grouped.get(identity);
    if (group) group.findings.push(finding);
    else grouped.set(identity, { identity, findings: [finding] });
  }
  const compare = (a: Finding, b: Finding) => {
    let result: number;
    switch (sort.key) {
      case 'risk':
        result = a.score.total - b.score.total;
        break;
      case 'tier':
        result = a.tier.localeCompare(b.tier);
        break;
      case 'identity':
        result = a.entitlement.identity_id.localeCompare(
          b.entitlement.identity_id,
        );
        break;
      case 'stage':
        result = stagePosition(a) - stagePosition(b);
        break;
    }
    return (
      (sort.descending ? -result : result) ||
      a.finding_id.localeCompare(b.finding_id)
    );
  };
  const groups = Array.from(grouped.values());
  for (const group of groups) group.findings.sort(compare);
  groups.sort((a, b) => compare(a.findings[0], b.findings[0]));
  return {
    groups,
    byId,
    tierCounts,
    sourceCounts,
    latestCapture,
    captured,
    systems,
    count,
    identities: groups.length,
  };
}
export function healthMetrics(metrics: Metrics) {
  const counts = metrics.counts;
  const planted = counts?.planted ?? 0;
  const executed = counts?.executed ?? 0;
  const revocations = counts?.revocations ?? 0;
  const percentage = (value: number) => `${Number(value.toFixed(1))}%`;
  return [
    {
      label: 'Recall',
      state: planted ? 'measured' : 'not-wired',
      value: planted
        ? `${percentage(metrics.drift_recall)} (${counts?.detected}/${planted})`
        : 'not instrumented',
    },
    {
      label: 'False revoke',
      state: executed ? 'measured' : 'no-events',
      value: executed
        ? `${percentage(metrics.false_revocation_rate)} (${Math.round((metrics.false_revocation_rate * executed) / 100)}/${executed})`
        : 'no revokes yet',
    },
    {
      label: 'Reversible',
      state: executed ? 'measured' : 'no-events',
      value: executed
        ? `${percentage(metrics.reversibility)} (${counts?.rollback_success}/${executed})`
        : 'no revokes yet',
    },
    {
      label: 'MTTR',
      state: revocations ? 'measured' : 'no-events',
      value: revocations
        ? `${metrics.mean_time_to_revocation} (${revocations}/${revocations})`
        : 'no revokes yet',
    },
    { label: 'Decision time', state: 'not-wired', value: 'not instrumented' },
  ];
}
export function errorMessage(error: unknown): string {
  const code =
    typeof error === 'object' && error !== null && 'code' in error
      ? error.code
      : '';
  if (code === 'TIMEOUT')
    return 'The capture service took too long to respond. Retry the request.';
  if (code === 'NETWORK_ERROR')
    return 'The capture service could not be reached. Check your connection and retry.';
  if (code === 'UNAUTHORIZED' || code === 'AUTH_REQUIRED')
    return 'Your session has expired. Sign in again to refresh the capture.';
  if (code === 'INVALID_RESPONSE')
    return 'The capture service returned unreadable data. Retry the request.';
  return `${error instanceof Error ? error.message.replace(/[.!]$/, '') : 'The request could not be completed'}. Retry the request; if it fails again, check the provider in Connections.`;
}
