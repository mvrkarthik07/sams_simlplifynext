import { memo, startTransition, useDeferredValue, useEffect, useMemo, useState } from 'react';
import { ArrowUpDown, Check, CircleDot, RefreshCw } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { ProvenanceBadge } from '../components/ProvenanceBadge';
import { api } from '../lib/api';
import type { Finding, Metrics, Tier } from '../lib/types';

type SortKey = 'score' | 'tier' | 'identity' | 'system' | 'stage';
type TierFilter = 'all' | Tier;
type SourceFilter = 'all' | 'captured' | 'fixture';

const TIER_ORDER: Tier[] = ['T0', 'T1', 'T2', 'T3'];
const tierLabel: Record<Tier, string> = {
  T0: 'Observe',
  T1: 'Auto-downgrade',
  T2: 'Broker',
  T3: 'Page',
};

/** Score-band vocabulary, single source of truth for both the numeral cell and the risk bar. */
const RISK_BANDS = [
  { ceiling: 30, key: 'low', label: 'low' },
  { ceiling: 60, key: 'elevated', label: 'elevated' },
  { ceiling: 85, key: 'high', label: 'high' },
  { ceiling: Infinity, key: 'critical', label: 'critical' },
] as const;

function riskBand(score: number): (typeof RISK_BANDS)[number] {
  return RISK_BANDS.find((band) => score < band.ceiling) ?? RISK_BANDS[RISK_BANDS.length - 1];
}

/** Below this many samples a MEASURED metric is still shown, qualified as low-confidence. */
const LOW_SAMPLE_THRESHOLD = 10;
type MetricState = 'measured' | 'no-events' | 'not-wired';
interface KpiDatum {
  key: string;
  label: string;
  state: MetricState;
  value: string;
  caption: string;
  primary?: boolean;
}

function isCaptured(finding: Finding): boolean {
  return finding.source === 'captured' || finding.entitlement.raw.source === 'captured';
}

/** Some resource identifiers are internal URNs (e.g. "github:pat:mvrkarthik07" for a
 * credential's own token entitlement). The table shows a readable description; the raw
 * URN stays available on the finding detail page only. */
const CREDENTIAL_URN = /^([a-z0-9-]+):(pat|oauth|token|api-key):(.+)$/i;
const CREDENTIAL_KIND_LABEL: Record<string, string> = {
  pat: 'Personal access token',
  oauth: 'OAuth grant',
  token: 'Access token',
  'api-key': 'API key',
};
function resourceLabel(resource: string): string {
  const match = resource.match(CREDENTIAL_URN);
  if (!match) return resource;
  const [, , kind, owner] = match;
  return `${CREDENTIAL_KIND_LABEL[kind.toLowerCase()] ?? 'Credential'} — ${owner}`;
}

function sampleQualifier(n: number): string {
  return n < LOW_SAMPLE_THRESHOLD ? ` (low sample, n=${n})` : ` (n=${n})`;
}

function buildKpis(metrics: Metrics): KpiDatum[] {
  const counts = metrics.counts;
  const planted = counts?.planted ?? 0;
  const detected = counts?.detected ?? 0;
  const executed = counts?.executed ?? 0;
  const revocations = counts?.revocations ?? 0;

  const recallState: MetricState = planted > 0 ? 'measured' : 'not-wired';
  const executionState: MetricState = executed > 0 ? 'measured' : 'no-events';
  const revocationState: MetricState = revocations > 0 ? 'measured' : 'no-events';

  return [
    {
      key: 'recall',
      label: 'Drift recall',
      state: recallState,
      primary: true,
      value: recallState === 'measured' ? `${metrics.drift_recall}%` : 'Not instrumented',
      caption: recallState === 'measured'
        ? `${detected} of ${planted} planted findings caught${sampleQualifier(planted)}, against a 95% target.`
        : 'No planted-finding rehearsal has run, so recall cannot be compared to its 95% target.',
    },
    {
      key: 'false-revoke',
      label: 'False revoke',
      state: executionState,
      value: executionState === 'measured' ? `${metrics.false_revocation_rate}%` : 'No revokes yet',
      caption: executionState === 'measured'
        ? `Checked ${executed} executions against ratified access${sampleQualifier(executed)}; target is 0%.`
        : 'Nothing has executed yet, so a false-revoke rate would be fabricated.',
    },
    {
      key: 'mean-time-to-revoke',
      label: 'Mean time to revoke',
      state: revocationState,
      value: revocationState === 'measured' ? metrics.mean_time_to_revocation : 'No revokes yet',
      caption: revocationState === 'measured'
        ? `Averaged across ${revocations} revocations${sampleQualifier(revocations)}.`
        : 'No revocation has completed, so there is no duration to average.',
    },
    {
      key: 'decision-time',
      label: 'Decision time',
      state: 'not-wired',
      value: 'Not instrumented',
      caption: 'Operator decision timing has no event source wired up yet. Target is under 2 minutes.',
    },
    {
      key: 'reversibility',
      label: 'Reversibility',
      state: executionState,
      value: executionState === 'measured' ? `${metrics.reversibility}%` : 'No revokes yet',
      caption: executionState === 'measured'
        ? `${executed} executions rolled back cleanly${sampleQualifier(executed)}; target is 100%.`
        : 'Nothing has executed yet, so there is nothing to roll back.',
    },
    {
      key: 'sandbox-cost',
      label: 'Sandbox cost',
      state: 'not-wired',
      value: 'Not instrumented',
      caption: 'Spend tracking is not wired to a billing source yet. Target is under $8 per rehearsal.',
    },
  ];
}

export function Dashboard() {
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [findings, setFindings] = useState<Finding[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<{ code?: string; message: string } | null>(null);
  const [sort, setSort] = useState<{ key: SortKey; descending: boolean }>({ key: 'score', descending: true });
  const [tierFilter, setTierFilter] = useState<TierFilter>('all');
  const [sourceFilter, setSourceFilter] = useState<SourceFilter>('all');
  const navigate = useNavigate();

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const [nextMetrics, nextFindings] = await Promise.all([api.getMetrics(), api.getFindings()]);
      setMetrics(nextMetrics);
      setFindings(nextFindings);
    } catch (reason) {
      setError({
        code: typeof reason === 'object' && reason !== null && 'code' in reason ? String(reason.code) : undefined,
        message: reason instanceof Error ? reason.message : 'Unable to load dashboard data.',
      });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void load(); }, []);

  const liveCount = findings.filter(isCaptured).length;
  const fixtureCount = findings.length - liveCount;
  const sourceLabel = liveCount > 0 ? 'GitHub capture' : fixtureCount > 0 ? 'Offline fixture' : 'No source loaded';
  const hasBothSources = liveCount > 0 && fixtureCount > 0;

  // Single traversal: filtering, per-axis chip counts, and tier bucketing all
  // happen in one pass over `findings`. Sorting the already-filtered subset
  // is a necessary, much smaller, second pass.
  const { rows, tierCounts, sourceCounts } = useMemo(() => {
    const tierCounts: Record<TierFilter, number> = { all: 0, T0: 0, T1: 0, T2: 0, T3: 0 };
    const sourceCounts: Record<SourceFilter, number> = { all: 0, captured: 0, fixture: 0 };
    const matched: Finding[] = [];
    for (const finding of findings) {
      const sourceKey: 'captured' | 'fixture' = isCaptured(finding) ? 'captured' : 'fixture';
      const matchesSource = sourceFilter === 'all' || sourceFilter === sourceKey;
      const matchesTier = tierFilter === 'all' || tierFilter === finding.tier;
      if (matchesSource) {
        tierCounts.all += 1;
        tierCounts[finding.tier] += 1;
      }
      if (matchesTier) {
        sourceCounts.all += 1;
        sourceCounts[sourceKey] += 1;
      }
      if (matchesSource && matchesTier) matched.push(finding);
    }
    const sorted = [...matched].sort((left, right) => {
      const values: Record<SortKey, [string | number, string | number]> = {
        score: [left.score.total, right.score.total],
        tier: [TIER_ORDER.indexOf(left.tier), TIER_ORDER.indexOf(right.tier)],
        identity: [left.entitlement.identity_id, right.entitlement.identity_id],
        system: [left.entitlement.system, right.entitlement.system],
        stage: [left.current_stage, right.current_stage],
      };
      const [a, b] = values[sort.key];
      const result = typeof a === 'number' && typeof b === 'number' ? a - b : String(a).localeCompare(String(b));
      return sort.descending ? -result : result;
    });
    return { rows: sorted, tierCounts, sourceCounts };
  }, [findings, tierFilter, sourceFilter, sort]);

  // Keeps filter-chip clicks responsive by letting React defer re-rendering
  // the (potentially large) row list behind the interaction.
  const deferredRows = useDeferredValue(rows);

  const setSortKey = (key: SortKey) => {
    startTransition(() => {
      setSort((current) => current.key === key
        ? { key, descending: !current.descending }
        : { key, descending: key === 'score' || key === 'tier' });
    });
  };

  const clearFilters = () => {
    startTransition(() => {
      setTierFilter('all');
      setSourceFilter('all');
    });
  };

  return (
    <div className="app-page dashboard-page">
      <header className="dashboard-header">
        <div>
          <h1 className="dashboard-title">Entitlement register</h1>
          <p className="dashboard-subtitle">Access drift found since the last capture, with a remediation plan pre-staged for each row.</p>
        </div>
        <div className="dashboard-actions">
          <div className="source-summary">
            <span className="source-summary-label">Current source</span>
            <span className="source-summary-value"><CircleDot size={13} aria-hidden="true" /> {sourceLabel}</span>
            <span className="source-summary-note">{findings.length} findings loaded</span>
          </div>
          <button type="button" className="outline-button refresh-button" onClick={() => void load()} disabled={loading}>
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} aria-hidden="true" /> Refresh
          </button>
        </div>
      </header>

      {loading && <DashboardSkeleton />}

      {error && !loading && (
        <section className="error-panel" role="alert">
          <p className="font-semibold">Dashboard data could not be loaded.</p>
          <p className="mt-1 font-mono text-xs text-destructive">{error.code ? `${error.code}: ` : ''}{error.message}</p>
          <button type="button" onClick={() => void load()} className="outline-button mt-4">Retry</button>
        </section>
      )}

      {!loading && !error && metrics && (
        <>
          <MetricsRule metrics={metrics} />
          <section className="findings-section" aria-labelledby="findings-heading">
            <div className="section-heading">
              <div>
                <h2 id="findings-heading">Drift findings</h2>
                <p className="muted section-description">{deferredRows.length} of {findings.length} findings shown.</p>
              </div>
              <div className="provenance-legend" aria-label="Provenance legend">
                {liveCount > 0 && <span><span className="provenance-live" aria-hidden="true">●</span> Owner capture ({liveCount})</span>}
                {fixtureCount > 0 && <span><span className="provenance-simulated" aria-hidden="true">●</span> Offline fixture ({fixtureCount})</span>}
              </div>
            </div>

            <div className="filter-groups">
              <div className="filter-group" role="group" aria-label="Filter by policy tier">
                <span className="filter-label">Tier</span>
                <FilterChip label="All tiers" count={tierCounts.all} active={tierFilter === 'all'} onClick={() => startTransition(() => setTierFilter('all'))} />
                {TIER_ORDER.slice().reverse().map((tier) => (
                  <FilterChip key={tier} label={`${tier} ${tierLabel[tier]}`} count={tierCounts[tier]} active={tierFilter === tier} onClick={() => startTransition(() => setTierFilter(tier))} />
                ))}
              </div>
              {hasBothSources && (
                <div className="filter-group" role="group" aria-label="Filter by data source">
                  <span className="filter-label">Source</span>
                  <FilterChip label="All sources" count={sourceCounts.all} active={sourceFilter === 'all'} onClick={() => startTransition(() => setSourceFilter('all'))} />
                  <FilterChip label="Owner capture" count={sourceCounts.captured} active={sourceFilter === 'captured'} onClick={() => startTransition(() => setSourceFilter('captured'))} />
                  <FilterChip label="Offline fixture" count={sourceCounts.fixture} active={sourceFilter === 'fixture'} onClick={() => startTransition(() => setSourceFilter('fixture'))} />
                </div>
              )}
            </div>

            <div className="data-table">
              <table>
                <thead>
                  <tr>
                    <SortableHeader label="Risk score" sortKey="score" sort={sort} onSort={setSortKey} />
                    <SortableHeader label="Tier" sortKey="tier" sort={sort} onSort={setSortKey} />
                    <SortableHeader label="Identity" sortKey="identity" sort={sort} onSort={setSortKey} />
                    <SortableHeader label="System and entitlement" sortKey="system" sort={sort} onSort={setSortKey} />
                    <SortableHeader label="Stage" sortKey="stage" sort={sort} onSort={setSortKey} />
                    <th scope="col"><span className="sr-only">Action</span></th>
                  </tr>
                </thead>
                <tbody>
                  {deferredRows.length === 0 && (
                    <tr>
                      <td colSpan={6} className="empty-row">
                        <div className="empty-state">
                          <p className="font-semibold">No findings match the current filters.</p>
                          <p className="muted text-xs">{tierFilter !== 'all' || sourceFilter !== 'all' ? 'Tier and source filters are both active.' : 'No findings are loaded.'}</p>
                          {(tierFilter !== 'all' || sourceFilter !== 'all') && (
                            <button type="button" className="outline-button" onClick={clearFilters}>Clear filters</button>
                          )}
                        </div>
                      </td>
                    </tr>
                  )}
                  {deferredRows.map((finding) => (
                    <FindingRow
                      key={finding.finding_id}
                      finding={finding}
                      onOpen={() => navigate(`/finding/${finding.finding_id}`)}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}
    </div>
  );
}

function FilterChip({ label, count, active, onClick }: { label: string; count: number; active: boolean; onClick: () => void }) {
  return (
    <button type="button" className="filter-chip" aria-pressed={active} disabled={count === 0 && !active} onClick={onClick}>
      {active && <Check size={11} aria-hidden="true" />}
      {label} <span className="filter-chip-count">{count}</span>
    </button>
  );
}

const MetricsRule = memo(function MetricsRule({ metrics }: { metrics: Metrics }) {
  const kpis = useMemo(() => buildKpis(metrics), [metrics]);
  return (
    <div className="summary-rule" aria-label="Governance metrics">
      {kpis.map((kpi) => (
        <div key={kpi.key} className={`summary-cell summary-cell-${kpi.state}${kpi.primary ? ' summary-cell-primary' : ''}`}>
          <div className="summary-label">{kpi.label}</div>
          <div className="summary-value">{kpi.value}</div>
          <div className="summary-target">{kpi.caption}</div>
        </div>
      ))}
    </div>
  );
});

function SortableHeader({ label, sortKey, sort, onSort }: { label: string; sortKey: SortKey; sort: { key: SortKey; descending: boolean }; onSort: (key: SortKey) => void }) {
  const active = sort.key === sortKey;
  const ariaSort: 'ascending' | 'descending' | 'none' = active ? (sort.descending ? 'descending' : 'ascending') : 'none';
  return (
    <th scope="col" aria-sort={ariaSort}>
      <button type="button" className="sort-button" onClick={() => onSort(sortKey)} aria-label={`Sort by ${label}${active ? `, currently ${ariaSort}` : ''}`}>
        {label} <ArrowUpDown size={12} aria-hidden="true" />
      </button>
    </th>
  );
}

const FindingRow = memo(function FindingRow({ finding, onOpen }: { finding: Finding; onOpen: () => void }) {
  const band = riskBand(finding.score.total);
  return (
    <tr>
      <td data-label="Risk score">
        <div className={`risk-cell risk-band-${band.key}`}>
          <span className="risk-bar-track"><span className="risk-bar-fill" style={{ height: `${Math.max(4, finding.score.total)}%` }} /></span>
          <span className="risk-score-group">
            <span className="risk-score">{finding.score.total.toFixed(0)}</span>
            <span className="risk-band-label">/ 100 · {band.label}</span>
          </span>
        </div>
      </td>
      <td data-label="Tier">
        <span className={`tier-badge tier-${finding.tier}`}>{finding.tier} {tierLabel[finding.tier]}</span>
        {finding.observe_only && <span className="tier-badge tier-T0" style={{ marginLeft: 6 }}>Non-revocable</span>}
      </td>
      <td data-label="Identity">
        <span className="identity-name">{finding.entitlement.identity_id}</span>
        <div className="muted mt-1 font-mono text-xs">{finding.finding_id}</div>
      </td>
      <td data-label="System and entitlement">
        <div className="entitlement-line">
          <span className="system-name">{finding.entitlement.system}</span>
          <code>{finding.entitlement.scope}</code>
          <ProvenanceBadge finding={finding} />
        </div>
        <div className="muted mt-1 max-w-md truncate font-mono text-xs" title={resourceLabel(finding.entitlement.resource)}>{resourceLabel(finding.entitlement.resource)}</div>
      </td>
      <td data-label="Stage">
        <span className="stage-name">{finding.current_stage}</span>
        <div className="muted mt-1 text-xs">{finding.stage_status}</div>
      </td>
      <td className="text-right">
        <button
          type="button"
          className="outline-button review-button"
          onClick={onOpen}
          aria-label={`Review finding ${finding.finding_id} — ${finding.entitlement.identity_id} on ${finding.entitlement.system}`}
        >
          Review
        </button>
      </td>
    </tr>
  );
});

function DashboardSkeleton() {
  return <div aria-label="Loading dashboard" className="dashboard-skeleton"><div /><div /></div>;
}
