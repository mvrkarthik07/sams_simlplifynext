export interface AuthSession {
  idToken: string;
  expiresAt: number;
}

interface CognitoAuthResult {
  AuthenticationResult?: {
    IdToken?: string;
    ExpiresIn?: number;
  };
  ChallengeName?: string;
}

interface CognitoSignUpResult {
  UserConfirmed?: boolean;
}

const SESSION_KEY = 'deadbolt.auth.session';
export const authRequired = import.meta.env.VITE_AUTH_REQUIRED === '1';
const region = String(import.meta.env.VITE_COGNITO_REGION ?? '');
const userPoolId = String(import.meta.env.VITE_COGNITO_USER_POOL_ID ?? '');
const clientId = String(import.meta.env.VITE_COGNITO_CLIENT_ID ?? '');

export const isAuthConfigured = authRequired && Boolean(region && userPoolId && clientId);
export const isUnauthenticatedMode = !authRequired;

const sessionStorageAvailable = (): boolean => typeof window !== 'undefined' && Boolean(window.sessionStorage);

export function getSession(): AuthSession | null {
  if (!sessionStorageAvailable()) return null;
  const raw = window.sessionStorage.getItem(SESSION_KEY);
  if (!raw) return null;
  try {
    const session = JSON.parse(raw) as Partial<AuthSession>;
    if (typeof session.idToken !== 'string' || typeof session.expiresAt !== 'number') return null;
    if (session.expiresAt <= Date.now()) {
      clearSession();
      return null;
    }
    return { idToken: session.idToken, expiresAt: session.expiresAt };
  } catch {
    clearSession();
    return null;
  }
}

export function getAccessToken(): string | null {
  return getSession()?.idToken ?? null;
}

export function clearSession(): void {
  if (sessionStorageAvailable()) window.sessionStorage.removeItem(SESSION_KEY);
}

function cognitoMessage(payload: unknown, fallback: string): string {
  if (typeof payload === 'object' && payload !== null && 'message' in payload) {
    const message = payload.message;
    if (typeof message === 'string' && message.trim()) return message;
  }
  return fallback;
}

async function cognitoRequest<T>(target: string, body: Record<string, unknown>): Promise<T> {
  if (!isAuthConfigured) throw new Error('Cognito authentication is not configured for this build.');
  const response = await fetch(`https://cognito-idp.${region}.amazonaws.com/`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/x-amz-json-1.1',
      'X-Amz-Target': `AWSCognitoIdentityProviderService.${target}`,
    },
    body: JSON.stringify(body),
  });
  const payload = await response.json() as unknown;
  if (!response.ok) throw new Error(cognitoMessage(payload, 'Cognito request failed.'));
  return payload as T;
}

export async function signIn(username: string, password: string): Promise<AuthSession> {
  const payload = await cognitoRequest<CognitoAuthResult>('InitiateAuth', {
      AuthFlow: 'USER_PASSWORD_AUTH',
      ClientId: clientId,
      AuthParameters: { USERNAME: username, PASSWORD: password },
  });
  const result = typeof payload === 'object' && payload !== null && 'AuthenticationResult' in payload
    ? payload as CognitoAuthResult
    : null;
  if (!result?.AuthenticationResult?.IdToken) throw new Error('Sign-in failed.');
  if (result.ChallengeName) throw new Error('This account requires an unsupported sign-in challenge.');
  const session: AuthSession = {
    idToken: result.AuthenticationResult.IdToken,
    expiresAt: Date.now() + (result.AuthenticationResult.ExpiresIn ?? 3600) * 1000,
  };
  window.sessionStorage.setItem(SESSION_KEY, JSON.stringify(session));
  return session;
}

export async function signUp(username: string, password: string): Promise<boolean> {
  const result = await cognitoRequest<CognitoSignUpResult>('SignUp', {
    ClientId: clientId,
    Username: username,
    Password: password,
    UserAttributes: [{ Name: 'email', Value: username }],
  });
  return result.UserConfirmed === true;
}

export async function confirmSignUp(username: string, code: string): Promise<void> {
  await cognitoRequest('ConfirmSignUp', {
    ClientId: clientId,
    Username: username,
    ConfirmationCode: code,
  });
}
