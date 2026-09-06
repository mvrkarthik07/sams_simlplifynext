import type { AuditLogEntry, ConnectionProvider, ConnectionScanResult, ConnectionSummary, ConnectionTestResult, Finding, Metrics, Plan } from './types';
import { getAccessToken } from './auth';

type ApiErrorCode =
  | 'INVALID_REQUEST'
  | 'NOT_FOUND'
  | 'INTERNAL_ERROR'
  | 'NETWORK_ERROR'
  | 'TIMEOUT'
  | 'INVALID_RESPONSE';

interface ApiErrorBody {
  code: ApiErrorCode | string;
  message: string;
}

interface ApiEnvelope<T> {
  data: T | null;
  error: ApiErrorBody | null;
}

export class DeadboltApiError extends Error {
  readonly code: ApiErrorCode | string;
  readonly status: number | undefined;
  readonly retriable: boolean;

  constructor(code: ApiErrorCode | string, message: string, status?: number, retriable = false) {
    super(message);
    this.name = 'DeadboltApiError';
    this.code = code;
    this.status = status;
    this.retriable = retriable;
  }
}

const configuredApiUrl = import.meta.env.VITE_API_BASE as string | undefined;
const API_BASE_URL = (configuredApiUrl || '/api').replace(/\/$/, '');
const REQUEST_TIMEOUT_MS = 8000;

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null;

const isApiEnvelope = (value: unknown): value is ApiEnvelope<unknown> => {
  if (!isRecord(value) || !('data' in value) || !('error' in value)) return false;
  const error = value.error;
  return error === null || (
    isRecord(error) &&
    typeof error.code === 'string' &&
    typeof error.message === 'string'
  );
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  for (let attempt = 0; attempt < 2; attempt += 1) {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    try {
      const token = getAccessToken();
      const response = await fetch(`${API_BASE_URL}${path}`, {
        ...init,
        signal: controller.signal,
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
          ...init?.headers,
        },
      });
      let payload: unknown;
      try {
        payload = await response.json();
      } catch {
        throw new DeadboltApiError(
          'INVALID_RESPONSE',
          'The API returned an invalid response.',
          response.status,
        );
      }
      if (!isApiEnvelope(payload)) {
        throw new DeadboltApiError('INVALID_RESPONSE', 'The API returned an invalid response.', response.status);
      }
      if (payload.error !== null || !response.ok) {
        const error = payload.error ?? { code: 'HTTP_ERROR', message: `Request failed (${response.status})` };
        throw new DeadboltApiError(error.code, error.message, response.status);
      }
      return payload.data as T;
    } catch (error) {
      if (error instanceof DeadboltApiError) throw error;
      if (error instanceof DOMException && error.name === 'AbortError') {
        throw new DeadboltApiError('TIMEOUT', 'The API request timed out.', undefined, false);
      }
      if (attempt === 0) continue;
      throw new DeadboltApiError('NETWORK_ERROR', 'The API could not be reached.', undefined, true);
    } finally {
      window.clearTimeout(timeout);
    }
  }
  throw new DeadboltApiError('NETWORK_ERROR', 'The API could not be reached.', undefined, true);
}

const id = (findingId: string) => encodeURIComponent(findingId);

const remoteApi = {
  getFindings: (): Promise<Finding[]> => request<Finding[]>('/findings'),
  getFinding: async (findingId: string): Promise<Finding | undefined> => {
    try {
      return await request<Finding>(`/findings/${id(findingId)}`);
    } catch (error) {
      if (error instanceof DeadboltApiError && error.code === 'NOT_FOUND') return undefined;
      throw error;
    }
  },
  getPlan: async (findingId: string): Promise<Plan | undefined> => {
    try {
      return await request<Plan>(`/plans/${id(findingId)}`);
    } catch (error) {
      if (error instanceof DeadboltApiError && error.code === 'NOT_FOUND') return undefined;
      throw error;
    }
  },
  getAuditLog: (): Promise<AuditLogEntry[]> => request<AuditLogEntry[]>('/audit'),
  getMetrics: (): Promise<Metrics> => request<Metrics>('/metrics'),
  decideApproval: (
    findingId: string,
    action: string,
    approver: string,
    reason?: string,
  ): Promise<Finding> => request<Finding>(`/findings/${id(findingId)}/decision`, {
    method: 'POST',
    body: JSON.stringify({ action, approver, reason: reason || '' }),
  }),
  rerunDriftEngine: (findingId: string): Promise<string | null> =>
    request<string | null>(`/findings/${id(findingId)}/rerun`, { method: 'POST' }),
  executeRollback: (findingId: string): Promise<Finding> =>
    request<Finding>(`/findings/${id(findingId)}/rollback`, { method: 'POST' }),
  getConnections: (): Promise<ConnectionSummary[]> => request<ConnectionSummary[]>('/connections'),
  saveConnection: (provider: ConnectionProvider, config: Record<string, unknown>): Promise<ConnectionSummary> =>
    request<ConnectionSummary>(`/connections/${provider}`, { method: 'POST', body: JSON.stringify(config) }),
  testConnection: (provider: ConnectionProvider): Promise<ConnectionTestResult> =>
    request<ConnectionTestResult>(`/connections/${provider}`, { method: 'POST', body: JSON.stringify({ action: 'test' }) }),
  scanConnection: (provider: ConnectionProvider): Promise<ConnectionScanResult> =>
    request<ConnectionScanResult>(`/connections/${provider}`, { method: 'POST', body: JSON.stringify({ action: 'scan' }) }),
  removeConnection: (provider: ConnectionProvider): Promise<ConnectionSummary> =>
    request<ConnectionSummary>(`/connections/${provider}`, { method: 'POST', body: JSON.stringify({ action: 'disconnect' }) }),
};

export const api = remoteApi;
