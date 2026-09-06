import {
  lazy,
  memo,
  Profiler,
  startTransition,
  Suspense,
  useCallback,
  useDeferredValue,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from 'react';
import type {
  KeyboardEvent,
  MouseEvent,
  ProfilerOnRenderCallback,
} from 'react';
import ArrowUpDown from 'lucide-react/dist/esm/icons/arrow-up-down.mjs';
import ArrowUpRight from 'lucide-react/dist/esm/icons/arrow-up-right.mjs';
import ChevronDown from 'lucide-react/dist/esm/icons/chevron-down.mjs';
import RefreshCw from 'lucide-react/dist/esm/icons/refresh-cw.mjs';
import Search from 'lucide-react/dist/esm/icons/search.mjs';
import LockKeyhole from 'lucide-react/dist/esm/icons/lock-keyhole.mjs';
import { useNavigate } from 'react-router-dom';
import { api, DATA_CHANGED_EVENT } from '../lib/api';
import { isUnauthenticatedMode } from '../lib/auth';
import type { Finding, Metrics, ConnectionProvider } from '../lib/types';
import {
  absoluteTime,
  captureTime,
  deriveRegister,
  entitlementLabel,
  errorMessage,
  healthMetrics,
  relativeTime,
  riskBand,
  shortTime,
  SOURCE_NAMES,
  stageLabel,
  stagePosition,
  STALE_MS,
  TIERS,
  TIER_NAMES,
} from '../lib/register';
import type { Sort, SortKey, SourceFilter, TierFilter } from '../lib/register';

const mobileQuery = '(max-width: 767px)';
const subscribeViewport = (notify: () => void) => {
  const query = window.matchMedia(mobileQuery);
  query.addEventListener('change', notify);
  return () => query.removeEventListener('change', notify);
};
const mobileSnapshot = () => window.matchMedia(mobileQuery).matches;
const serverSnapshot = () => false;
const subscribeScroll = (notify: () => void) => {
  let frame = 0;
  const changed = () => {
    cancelAnimationFrame(frame);
    frame = requestAnimationFrame(notify);
  };
  window.addEventListener('scroll', changed, { passive: true });
  window.addEventListener('resize', changed);
  return () => {
    cancelAnimationFrame(frame);
    window.removeEventListener('scroll', changed);
    window.removeEventListener('resize', changed);
  };
};
const scrollSnapshot = () =>
  `${Math.floor(window.scrollY / 88) * 88}:${window.innerWidth}:${window.innerHeight}`;
const serverScrollSnapshot = () => '0:1440:1000';

const loadDetail = () => import('../components/register/DetailPanel');
const DetailPanel = lazy(loadDetail);
let detailPreloaded = false;
function preloadDetail() {
  if (!detailPreloaded) {
    detailPreloaded = true;
    void loadDetail().catch(() => {
      detailPreloaded = false;
    });
  }
}
const recordProfile: ProfilerOnRenderCallback = (
  _id,
  phase,
  actualDuration,
  _baseDuration,
  startTime,
  commitTime,
) => {
  if (
    import.meta.env.DEV &&
    new URLSearchParams(location.search).has('profile')
  ) {
    window.dispatchEvent(
      new CustomEvent('deadbolt:profile', {
        detail: { phase, actualDuration, startTime, commitTime },
      }),
    );
  }
};
interface PendingDecision {
  finding: Finding;
  action: string;
  label: string;
  reason: string;
  expires: number;
}
interface Toast {
  text: string;
  error?: boolean;
}

export function Dashboard() {
  return (
    <Profiler id="entitlement-register" onRender={recordProfile}>
      <Register />
    </Profiler>
  );
}
function Register() {
  const mobile = useSyncExternalStore(
    subscribeViewport,
    mobileSnapshot,
    serverSnapshot,
  );
  const [payload, setPayload] = useState<{
    findings: Finding[];
    metrics: Metrics | null;
  }>({ findings: [], metrics: null });
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [now, setNow] = useState(Date.now);
  const [sort, setSort] = useState<Sort>({ key: 'risk', descending: true });
  const [tier, setTier] = useState<TierFilter>('all');
  const [source, setSource] = useState<SourceFilter>('all');
  const [search, setSearch] = useState('');
  const deferredSearch = useDeferredValue(search);
  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set());
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [pending, setPending] = useState<PendingDecision | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [toast, setToast] = useState<Toast | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const origin = useRef<HTMLElement | null>(null);
  const sequence = useRef(0);
  const pendingTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const navigate = useNavigate();

  const load = useCallback(async () => {
    const request = ++sequence.current;
    setRefreshing(true);
    try {
      const [findings, metrics] = await Promise.all([
        api.getFindings(),
        api.getMetrics(),
      ]);
      if (request !== sequence.current) return;
      setPayload({ findings, metrics });
      setError(null);
      setNow(Date.now());
    } catch (reason) {
      if (request === sequence.current) setError(errorMessage(reason));
    } finally {
      if (request === sequence.current) {
        setLoading(false);
        setRefreshing(false);
      }
    }
  }, []);
  useEffect(() => {
    let canceled = false;
    void Promise.resolve().then(() => {
      if (!canceled) void load();
    });
    const invalidate = () => {
      sequence.current++;
    };
    const changed = () => {
      void load();
    };
    const timer = window.setInterval(() => setNow(Date.now()), 30_000);
    const shortcut = (event: globalThis.KeyboardEvent) => {
      if (
        (event.metaKey || event.ctrlKey) &&
        event.key.toLowerCase() === 'k' &&
        !document.querySelector('dialog[open]')
      ) {
        event.preventDefault();
        searchRef.current?.focus();
      }
    };
    window.addEventListener(DATA_CHANGED_EVENT, changed);
    window.addEventListener('keydown', shortcut);
    return () => {
      canceled = true;
      invalidate();
      window.clearInterval(timer);
      if (pendingTimer.current) clearTimeout(pendingTimer.current);
      window.removeEventListener(DATA_CHANGED_EVENT, changed);
      window.removeEventListener('keydown', shortcut);
    };
  }, [load]);
  useEffect(() => {
    if (!toast || toast.error) return;
    const timer = window.setTimeout(() => setToast(null), 8000);
    return () => window.clearTimeout(timer);
  }, [toast]);

  const view = useMemo(
    () => deriveRegister(payload.findings, tier, source, deferredSearch, sort),
    [payload.findings, tier, source, deferredSearch, sort],
  );
  const latest = view.latestCapture;
  const stale = latest !== null && now - latest > STALE_MS;
  const sourceName =
    view.systems.size === 1
      ? {
          github: 'GitHub',
          'aws-iam': 'AWS IAM',
          slack: 'Slack',
          notion: 'Notion',
          salesforce: 'Salesforce',
          workday: 'Workday',
        }[Array.from(view.systems)[0]]
      : `${view.systems.size} systems`;
  const selected = selectedId ? view.byId.get(selectedId) : undefined;
  const hasFilters = tier !== 'all' || source !== 'all' || search.trim() !== '';
  const clearFilters = () =>
    startTransition(() => {
      setTier('all');
      setSource('all');
      setSearch('');
    });
  const changeSort = useCallback(
    (key: SortKey) =>
      startTransition(() =>
        setSort((current) => ({
          key,
          descending:
            current.key === key
              ? !current.descending
              : key === 'risk' || key === 'tier',
        })),
      ),
    [],
  );
  const activate = useCallback(
    (event: MouseEvent<HTMLElement> | KeyboardEvent<HTMLElement>) => {
      if ('key' in event && event.key !== 'Enter' && event.key !== ' ') return;
      const target = (event.target as HTMLElement).closest<HTMLElement>(
        '[data-finding-id]',
      );
      if (!target || target.dataset.disabled === 'true') return;
      event.preventDefault();
      origin.current = target;
      setSelectedId(target.dataset.findingId ?? null);
    },
    [],
  );
  const toggleGroup = useCallback((event: MouseEvent<HTMLButtonElement>) => {
    const identity = event.currentTarget.dataset.identity;
    if (!identity) return;
    startTransition(() =>
      setCollapsed((current) => {
        const next = new Set(current);
        if (next.has(identity)) next.delete(identity);
        else next.add(identity);
        return next;
      }),
    );
  }, []);
  const closeDetail = useCallback(() => {
    setSelectedId(null);
    requestAnimationFrame(() => {
      if (origin.current?.isConnected) origin.current.focus();
      else searchRef.current?.focus();
    });
  }, []);
  const scheduleDecision = useCallback(
    (finding: Finding, action: string, label: string, reason = '') => {
      if (pendingTimer.current || submitting) return;
      setToast(null);
      setPending({
        finding,
        action,
        label,
        reason,
        expires: Date.now() + 8000,
      });
      pendingTimer.current = setTimeout(async () => {
        pendingTimer.current = null;
        setPending(null);
        setSubmitting(true);
        try {
          const updated =
            action === 'rollback'
              ? await api.executeRollback(finding.finding_id)
              : await api.decideApproval(
                  finding.finding_id,
                  action,
                  'Access Reviewer',
                  reason,
                );
          setPayload((current) => ({
            ...current,
            findings: current.findings.map((item) =>
              item.finding_id === updated.finding_id ? updated : item,
            ),
          }));
          setToast({ text: `${label} recorded for ${finding.finding_id}.` });
        } catch (reason) {
          setToast({
            text: `${label} was not recorded. ${errorMessage(reason)}`,
            error: true,
          });
        } finally {
          setSubmitting(false);
        }
      }, 8000);
    },
    [submitting],
  );
  const undo = () => {
    if (pendingTimer.current) clearTimeout(pendingTimer.current);
    pendingTimer.current = null;
    setToast({
      text: `${pending?.label ?? 'Decision'} canceled. No change was sent.`,
    });
    setPending(null);
  };
  const runCapture = async () => {
    if (!view.captured) {
      navigate('/connections');
      return;
    }
    setRefreshing(true);
    try {
      const connections = await api.getConnections();
      const configured = connections.find(
        (connection) =>
          connection.status === 'connected' &&
          view.systems.has(connection.provider),
      );
      if (!configured) {
        navigate('/connections');
        return;
      }
      await api.scanConnection(configured.provider as ConnectionProvider);
      await load();
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setRefreshing(false);
    }
  };

  const groupRows =
    view.count > 200 ? (
      <WindowedFindings
        mobile={mobile}
        groups={view.groups}
        collapsed={collapsed}
        onToggle={toggleGroup}
        selectedId={selectedId}
        pendingId={pending?.finding.finding_id ?? null}
        activate={activate}
        now={now}
      />
    ) : (
      view.groups.map((group) => (
        <FindingGroupRows
          key={group.identity}
          mobile={mobile}
          identity={group.identity}
          findings={group.findings}
          collapsed={collapsed.has(group.identity)}
          onToggle={toggleGroup}
          selectedId={selectedId}
          pendingId={pending?.finding.finding_id ?? null}
          activate={activate}
          now={now}
        />
      ))
    );
  return (
    <div className="register-page">
      <div className="register-content">
        <header className="register-header">
          <h1>Entitlement register</h1>
          <div className="capture-line">
            {loading ? (
              <span
                className="skeleton timestamp-skeleton"
                aria-label="Loading capture time"
              />
            ) : (
              <time
                title={
                  latest
                    ? absoluteTime(latest)
                    : 'No capture timestamp was returned'
                }
                dateTime={latest ? new Date(latest).toISOString() : undefined}
              >
                {latest
                  ? relativeTime(latest, now)
                  : 'Capture time unavailable'}
              </time>
            )}
            {!loading && payload.findings.length > 0 ? (
              <span>{sourceName}</span>
            ) : null}
            {!loading ? (
              <span
                className={`capture-status ${error ? 'status-error' : stale ? 'status-stale' : latest ? 'status-healthy' : 'muted'}`}
              >
                <span aria-hidden="true">
                  {error ? '✕' : stale ? '▲' : '●'}
                </span>{' '}
                {error
                  ? 'Failed'
                  : stale
                    ? 'Stale'
                    : latest
                      ? 'Connected'
                      : 'No capture'}
              </span>
            ) : null}
            <button
              className="register-icon-button"
              onClick={() => void load()}
              disabled={loading || refreshing}
              aria-label={refreshing ? 'Refreshing capture' : 'Refresh capture'}
              title="Refresh capture"
            >
              <RefreshCw
                size={16}
                className={refreshing && !loading ? 'refresh-spinning' : ''}
                aria-hidden="true"
              />
            </button>
          </div>
        </header>
        <div className="health-line" aria-label="Governance health">
          {loading ? (
            <>
              <span className="skeleton health-skeleton" />
              <span className="skeleton health-skeleton" />
              <span className="skeleton health-skeleton" />
            </>
          ) : payload.metrics ? (
            healthMetrics(payload.metrics).map((metric) => (
              <span className="health-metric" key={metric.label}>
                {metric.label}{' '}
                <span className={`metric-${metric.state}`}>{metric.value}</span>
              </span>
            ))
          ) : (
            <span className="metric-not-wired">
              Health metrics unavailable until the capture service responds.
            </span>
          )}
        </div>
        <section aria-label="Findings review queue" className="register-queue">
          <div className="register-toolbar">
            <div className="register-filters">
              <label>
                Tier{' '}
                <select
                  aria-label="Filter by tier"
                  value={tier}
                  onChange={(event) =>
                    startTransition(() =>
                      setTier(event.target.value as TierFilter),
                    )
                  }
                >
                  <option value="all">All ({view.tierCounts.all})</option>
                  {TIERS.map((value) => (
                    <option
                      key={value}
                      value={value}
                      disabled={view.tierCounts[value] === 0}
                    >
                      {value} {TIER_NAMES[value]} ({view.tierCounts[value]})
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Source{' '}
                <select
                  aria-label="Filter by source"
                  value={source}
                  onChange={(event) =>
                    startTransition(() =>
                      setSource(event.target.value as SourceFilter),
                    )
                  }
                >
                  {(['all', 'fixture', 'captured'] as const).map((value) => (
                    <option
                      key={value}
                      value={value}
                      disabled={
                        value !== 'all' && view.sourceCounts[value] === 0
                      }
                    >
                      {SOURCE_NAMES[value]} ({view.sourceCounts[value]})
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <p className="result-summary" aria-live="polite" aria-atomic="true">
              {loading
                ? 'Loading findings'
                : `${view.count} ${view.count === 1 ? 'finding' : 'findings'} · ${view.identities} ${view.identities === 1 ? 'identity' : 'identities'}`}
            </p>
            <label className="register-search">
              <Search size={14} aria-hidden="true" />
              <span className="sr-only">Search findings</span>
              <input
                ref={searchRef}
                type="search"
                placeholder="Search findings"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
              />
              <kbd aria-hidden="true">⌘K</kbd>
            </label>
            {mobile ? (
              <label className="mobile-sort-control">
                Sort{' '}
                <select
                  aria-label="Sort findings"
                  value={`${sort.key}:${sort.descending ? 'desc' : 'asc'}`}
                  onChange={(event) => {
                    const [key, direction] = event.target.value.split(':');
                    startTransition(() =>
                      setSort({
                        key: key as SortKey,
                        descending: direction === 'desc',
                      }),
                    );
                  }}
                >
                  {(['risk', 'tier', 'identity', 'stage'] as const).flatMap(
                    (key) => [
                      <option key={`${key}:desc`} value={`${key}:desc`}>
                        {key === 'risk'
                          ? 'Risk'
                          : key === 'tier'
                            ? 'Tier'
                            : key === 'identity'
                              ? 'Identity'
                              : 'Stage'}{' '}
                        descending
                      </option>,
                      <option key={`${key}:asc`} value={`${key}:asc`}>
                        {key === 'risk'
                          ? 'Risk'
                          : key === 'tier'
                            ? 'Tier'
                            : key === 'identity'
                              ? 'Identity'
                              : 'Stage'}{' '}
                        ascending
                      </option>,
                    ],
                  )}
                </select>
              </label>
            ) : null}
          </div>
          {error ? (
            <div className="capture-notice notice-error">
              <p>
                {error}{' '}
                {latest
                  ? `Showing last successful capture from ${shortTime(latest)} SGT.`
                  : 'No capture is available.'}
              </p>
              <button
                className="register-button"
                onClick={() => void load()}
                disabled={refreshing}
              >
                Retry
              </button>
            </div>
          ) : stale ? (
            <div className="capture-notice">
              <p>
                <span aria-hidden="true">▲</span> This capture is more than{' '}
                {STALE_MS / 60_000} minutes old. Refresh before making a
                decision.
              </p>
              <button
                className="register-button"
                onClick={() => void runCapture()}
                disabled={refreshing}
              >
                Run capture
              </button>
            </div>
          ) : null}
          <div className="register-table-container" aria-busy={loading}>
            {mobile ? (
              <ul
                className="register-cards"
                aria-label="Access drift findings grouped by identity"
              >
                {loading
                  ? Array.from({ length: 12 }, (_, index) => (
                      <li className="register-skeleton-row" key={index}>
                        <span className="skeleton" />
                      </li>
                    ))
                  : groupRows}
              </ul>
            ) : (
              <table
                className={`register-table${view.count > 200 ? ' windowed-table' : ''}`}
              >
                <caption className="sr-only">
                  Access drift findings grouped by identity
                </caption>
                <colgroup>
                  <col className="risk-column" />
                  <col className="tier-column" />
                  <col className="identity-column" />
                  <col className="entitlement-column" />
                  <col className="stage-column" />
                  <col className="action-column" />
                </colgroup>
                <thead>
                  <tr>
                    <SortHeader
                      label="Risk"
                      sortKey="risk"
                      sort={sort}
                      onSort={changeSort}
                    />
                    <SortHeader
                      label="Tier"
                      sortKey="tier"
                      sort={sort}
                      onSort={changeSort}
                    />
                    <SortHeader
                      label="Identity"
                      sortKey="identity"
                      sort={sort}
                      onSort={changeSort}
                    />
                    <th scope="col">Entitlement</th>
                    <SortHeader
                      label="Stage"
                      sortKey="stage"
                      sort={sort}
                      onSort={changeSort}
                    />
                    <th scope="col">
                      <span className="sr-only">Actions</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {loading
                    ? Array.from({ length: 12 }, (_, index) => (
                        <tr className="register-skeleton-row" key={index}>
                          <td colSpan={6}>
                            <span className="skeleton" />
                          </td>
                        </tr>
                      ))
                    : groupRows}
                </tbody>
              </table>
            )}
            {!loading && view.count === 0 ? (
              <div className="register-empty">
                <h2>
                  {hasFilters
                    ? `No findings match ${[tier !== 'all' ? `tier ${tier}` : '', source !== 'all' ? `source ${SOURCE_NAMES[source]}` : '', search.trim() ? `“${search.trim()}”` : ''].filter(Boolean).join(' and ')}`
                    : error
                      ? 'Capture unavailable.'
                      : 'Queue is clear.'}
                </h2>
                <p>
                  {hasFilters
                    ? 'Clear filters to return to the full queue.'
                    : error
                      ? 'Retry the capture request to load the queue.'
                      : `Last capture${latest ? ` at ${shortTime(latest)} SGT` : ''} found no drift.`}
                </p>
                <button
                  className="register-button"
                  onClick={
                    hasFilters
                      ? clearFilters
                      : error
                        ? () => void load()
                        : () => void runCapture()
                  }
                >
                  {hasFilters
                    ? 'Clear filters'
                    : error
                      ? 'Retry'
                      : 'Run capture'}
                </button>
              </div>
            ) : null}
          </div>
        </section>
      </div>
      <footer className="register-footer">
        <span>{view.captured ? 'Live capture' : 'Fixture'}</span>
        <span>
          {latest
            ? `Last capture ${shortTime(latest)} SGT`
            : 'Capture time unavailable'}
        </span>
        {isUnauthenticatedMode ? <span>Preview access</span> : null}
        <span className="footer-shortcut">⌘K search</span>
      </footer>
      {pending || toast ? (
        <div className={`register-toast${toast?.error ? ' toast-error' : ''}`}>
          <p>
            {pending
              ? `${pending.label} scheduled for ${pending.finding.finding_id}. Sending after 8 seconds.`
              : toast?.text}
          </p>
          {pending ? (
            <button className="register-button" onClick={undo}>
              Undo
            </button>
          ) : (
            <button className="register-button" onClick={() => setToast(null)}>
              Dismiss
            </button>
          )}
        </div>
      ) : null}
      {selected ? (
        <Suspense
          fallback={
            <div className="panel-loading">Loading finding details…</div>
          }
        >
          <DetailPanel
            key={selected.finding_id}
            finding={selected}
            onClose={closeDetail}
            onDecision={scheduleDecision}
            busy={pending !== null || submitting}
            pendingLabel={
              pending?.finding.finding_id === selected.finding_id
                ? pending.label
                : null
            }
            onUndo={undo}
            feedback={toast}
            onDismiss={() => setToast(null)}
          />
        </Suspense>
      ) : null}
    </div>
  );
}

interface WindowedProps {
  mobile: boolean;
  groups: { identity: string; findings: Finding[] }[];
  collapsed: Set<string>;
  onToggle: (event: MouseEvent<HTMLButtonElement>) => void;
  selectedId: string | null;
  pendingId: string | null;
  activate: (
    event: MouseEvent<HTMLElement> | KeyboardEvent<HTMLElement>,
  ) => void;
  now: number;
}
/** Window only presentation for large queues. All records still participate in the single
 * derivation above. Scroll position is an external store, never effect-derived React state. */
const WindowedFindings = memo(function WindowedFindings({
  mobile,
  groups,
  collapsed,
  onToggle,
  selectedId,
  pendingId,
  activate,
  now,
}: WindowedProps) {
  const viewport = useSyncExternalStore(
    subscribeScroll,
    scrollSnapshot,
    serverScrollSnapshot,
  );
  const [scrollY, width, height] = viewport.split(':').map(Number);
  const [origin, setOrigin] = useState(250);
  const bindSentinel = useCallback((node: HTMLElement | null) => {
    if (node) setOrigin(node.getBoundingClientRect().top + window.scrollY);
  }, []);
  const rowHeight = mobile ? 140 : width < 1200 ? 54 : 44;
  const start = Math.max(0, scrollY - origin - 600);
  const end = scrollY - origin + height + 600;
  let offset = 0;
  let firstVisible = -1;
  let lastVisibleEnd = 0;
  const rendered: React.ReactNode[] = [];
  for (const group of groups) {
    if (group.findings.length > 1) {
      const groupHeight = mobile ? 40 : 32;
      if (offset + groupHeight >= start && offset <= end) {
        if (firstVisible < 0) firstVisible = offset;
        const button = (
          <button
            data-identity={group.identity}
            onClick={onToggle}
            aria-expanded={!collapsed.has(group.identity)}
          >
            <ChevronDown
              size={14}
              className={collapsed.has(group.identity) ? 'group-closed' : ''}
              aria-hidden="true"
            />
            <span>{group.identity}</span>
            <span className="group-count">
              {group.findings.length} findings
            </span>
          </button>
        );
        rendered.push(
          mobile ? (
            <li className="identity-group" key={`group:${group.identity}`}>
              {button}
            </li>
          ) : (
            <tr className="identity-group" key={`group:${group.identity}`}>
              <th colSpan={6} scope="rowgroup">
                {button}
              </th>
            </tr>
          ),
        );
        lastVisibleEnd = offset + groupHeight;
      }
      offset += groupHeight;
      if (collapsed.has(group.identity)) continue;
    }
    for (const finding of group.findings) {
      if (offset + rowHeight >= start && offset <= end) {
        if (firstVisible < 0) firstVisible = offset;
        const props = {
          finding,
          selected: finding.finding_id === selectedId,
          pending: finding.finding_id === pendingId,
          activate,
          now,
        };
        rendered.push(
          mobile ? (
            <FindingCard key={finding.finding_id} {...props} />
          ) : (
            <FindingRow key={finding.finding_id} {...props} />
          ),
        );
        lastVisibleEnd = offset + rowHeight;
      }
      offset += rowHeight;
    }
  }
  const before = Math.max(0, firstVisible);
  const after = Math.max(0, offset - lastVisibleEnd);
  return mobile ? (
    <>
      <li
        ref={bindSentinel}
        aria-hidden="true"
        className="virtual-spacer"
        style={{ height: before }}
      />
      {rendered}
      <li
        aria-hidden="true"
        className="virtual-spacer"
        style={{ height: after }}
      />
    </>
  ) : (
    <>
      <tr
        ref={bindSentinel}
        aria-hidden="true"
        className="virtual-spacer"
        style={{ height: before }}
      >
        <td colSpan={6} />
      </tr>
      {rendered}
      <tr
        aria-hidden="true"
        className="virtual-spacer"
        style={{ height: after }}
      >
        <td colSpan={6} />
      </tr>
    </>
  );
});

const FindingGroupRows = memo(function FindingGroupRows({
  mobile = false,
  identity,
  findings,
  collapsed,
  onToggle,
  selectedId,
  pendingId,
  activate,
  now,
}: {
  mobile?: boolean;
  identity: string;
  findings: Finding[];
  collapsed: boolean;
  onToggle: (event: MouseEvent<HTMLButtonElement>) => void;
  selectedId: string | null;
  pendingId: string | null;
  activate: (
    event: MouseEvent<HTMLElement> | KeyboardEvent<HTMLElement>,
  ) => void;
  now: number;
}) {
  const groupButton = (
    <button
      data-identity={identity}
      onClick={onToggle}
      aria-expanded={!collapsed}
    >
      <ChevronDown
        size={14}
        className={collapsed ? 'group-closed' : ''}
        aria-hidden="true"
      />
      <span>{identity}</span>
      <span className="group-count">{findings.length} findings</span>
    </button>
  );
  return (
    <>
      {findings.length > 1 ? (
        mobile ? (
          <li className="identity-group">{groupButton}</li>
        ) : (
          <tr className="identity-group">
            <th colSpan={6} scope="rowgroup">
              {groupButton}
            </th>
          </tr>
        )
      ) : null}
      {collapsed && findings.length > 1
        ? null
        : findings.map((finding) =>
            mobile ? (
              <FindingCard
                key={finding.finding_id}
                finding={finding}
                selected={finding.finding_id === selectedId}
                pending={finding.finding_id === pendingId}
                activate={activate}
                now={now}
              />
            ) : (
              <FindingRow
                key={finding.finding_id}
                finding={finding}
                selected={finding.finding_id === selectedId}
                pending={finding.finding_id === pendingId}
                activate={activate}
                now={now}
              />
            ),
          )}
    </>
  );
});
const FindingRow = memo(function FindingRow({
  finding,
  selected,
  pending,
  activate,
  now,
}: {
  finding: Finding;
  selected: boolean;
  pending: boolean;
  activate: (
    event: MouseEvent<HTMLElement> | KeyboardEvent<HTMLElement>,
  ) => void;
  now: number;
}) {
  const band = riskBand(finding.score.total);
  const disabled = finding.entitlement.raw.unavailable === true;
  const time = captureTime(finding);
  const stale = time !== null && now - time > STALE_MS;
  return (
    <tr
      className={`finding-row risk-${band.key}`}
      data-finding-id={finding.finding_id}
      data-selected={selected}
      data-disabled={disabled}
      tabIndex={disabled ? -1 : 0}
      onClick={activate}
      onKeyDown={activate}
      onMouseEnter={preloadDetail}
      onFocus={preloadDetail}
      aria-label={`${finding.finding_id} for ${finding.entitlement.identity_id}${disabled ? ', unavailable' : ''}`}
    >
      <td className="finding-risk">
        <span className="risk-rail" aria-hidden="true">
          {Array.from({ length: band.ticks }, (_, index) => (
            <i key={index} />
          ))}
        </span>
        <div className="risk-value">
          <strong>{Math.round(finding.score.total)}</strong>
          <span>{band.key}</span>
          <span className={`tier-badge tier-${finding.tier} inline-tier`}>
            {finding.tier}
          </span>
        </div>
      </td>
      <td className="finding-tier">
        <span
          className={`tier-badge tier-${finding.tier}`}
          title={TIER_NAMES[finding.tier]}
        >
          {finding.tier}
        </span>
      </td>
      <td className="finding-identity">{finding.entitlement.identity_id}</td>
      <td className="finding-entitlement" title={entitlementLabel(finding)}>
        {entitlementLabel(finding)}
        {stale ? (
          <span
            className="row-stale"
            title={`Captured ${time ? absoluteTime(time) : ''}`}
          >
            <span aria-hidden="true">▲</span> Stale
          </span>
        ) : null}
      </td>
      <td className="finding-stage">
        <span>
          {pending
            ? 'Decision scheduled'
            : disabled
              ? 'Evidence unavailable'
              : stageLabel(finding)}
        </span>
        <span className="pipeline-position">{stagePosition(finding)}/5</span>
      </td>
      <td className="finding-action">
        <button
          className="register-icon-button"
          tabIndex={disabled ? -1 : 0}
          disabled={disabled}
          aria-label={`Review ${finding.finding_id} for ${finding.entitlement.identity_id}`}
        >
          {disabled ? (
            <LockKeyhole size={16} aria-hidden="true" />
          ) : (
            <ArrowUpRight size={16} aria-hidden="true" />
          )}
        </button>
      </td>
    </tr>
  );
});
const FindingCard = memo(function FindingCard({
  finding,
  selected,
  pending,
  activate,
  now,
}: {
  finding: Finding;
  selected: boolean;
  pending: boolean;
  activate: (
    event: MouseEvent<HTMLElement> | KeyboardEvent<HTMLElement>,
  ) => void;
  now: number;
}) {
  const band = riskBand(finding.score.total);
  const disabled = finding.entitlement.raw.unavailable === true;
  const time = captureTime(finding);
  const stale = time !== null && now - time > STALE_MS;
  return (
    <li
      className={`finding-row risk-${band.key}`}
      data-finding-id={finding.finding_id}
      data-selected={selected}
      data-disabled={disabled}
      tabIndex={disabled ? -1 : 0}
      onClick={activate}
      onKeyDown={activate}
      onMouseEnter={preloadDetail}
      onFocus={preloadDetail}
      aria-label={`${finding.finding_id} for ${finding.entitlement.identity_id}${disabled ? ', unavailable' : ''}`}
    >
      <div className="finding-risk">
        <span className="risk-rail" aria-hidden="true">
          {Array.from({ length: band.ticks }, (_, index) => (
            <i key={index} />
          ))}
        </span>
        <div className="risk-value">
          <strong>{Math.round(finding.score.total)}</strong>
          <span>{band.key}</span>
          <span className={`tier-badge tier-${finding.tier} inline-tier`}>
            {finding.tier}
          </span>
        </div>
      </div>
      <div className="finding-tier">
        <span
          className={`tier-badge tier-${finding.tier}`}
          title={TIER_NAMES[finding.tier]}
        >
          {finding.tier}
        </span>
      </div>
      <div className="finding-identity">{finding.entitlement.identity_id}</div>
      <div className="finding-entitlement" title={entitlementLabel(finding)}>
        {entitlementLabel(finding)}
        {stale ? (
          <span
            className="row-stale"
            title={`Captured ${time ? absoluteTime(time) : ''}`}
          >
            <span aria-hidden="true">▲</span> Stale
          </span>
        ) : null}
      </div>
      <div className="finding-stage">
        <span>
          {pending
            ? 'Decision scheduled'
            : disabled
              ? 'Evidence unavailable'
              : stageLabel(finding)}
        </span>
        <span className="pipeline-position">{stagePosition(finding)}/5</span>
      </div>
      <div className="finding-action">
        <button
          className="register-icon-button"
          tabIndex={disabled ? -1 : 0}
          disabled={disabled}
          aria-label={`Review ${finding.finding_id} for ${finding.entitlement.identity_id}`}
        >
          {disabled ? (
            <LockKeyhole size={16} aria-hidden="true" />
          ) : (
            <ArrowUpRight size={16} aria-hidden="true" />
          )}
        </button>
      </div>
    </li>
  );
});
function SortHeader({
  label,
  sortKey,
  sort,
  onSort,
}: {
  label: string;
  sortKey: SortKey;
  sort: Sort;
  onSort: (key: SortKey) => void;
}) {
  return (
    <th
      scope="col"
      className={sortKey === 'tier' ? 'finding-tier' : ''}
      aria-sort={
        sort.key === sortKey
          ? sort.descending
            ? 'descending'
            : 'ascending'
          : 'none'
      }
    >
      <button className="register-sort" onClick={() => onSort(sortKey)}>
        {label}
        <ArrowUpDown size={12} aria-hidden="true" />
      </button>
    </th>
  );
}
