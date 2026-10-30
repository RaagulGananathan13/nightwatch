// Sign-in with Cognito's hosted login page, using the authorization code flow with PKCE.
//
// The dashboard is a public client: no secret ships to the browser. Each sign-in makes a
// random verifier and state. Cognito returns a one-time code to this page, and the page trades
// it for tokens, proving with the verifier that it started the sign-in. Tokens are kept in
// sessionStorage, so they last for this browser tab only and are gone when it closes.

export interface Config {
  region: string;
  userPoolId: string;
  clientId: string;
  domain: string; // the hosted login page, e.g. nightwatch-123.auth.us-east-2.amazoncognito.com
}

export interface Tokens {
  access: string;
  refresh?: string;
  expiresAt: number; // epoch milliseconds
}

const TOKENS = "nightwatch.tokens";
const VERIFIER = "nightwatch.verifier";
const STATE = "nightwatch.state";

export function base64url(bytes: Uint8Array): string {
  let text = "";
  for (const byte of bytes) text += String.fromCharCode(byte);
  return btoa(text).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function randomString(byteCount = 32): string {
  return base64url(crypto.getRandomValues(new Uint8Array(byteCount)));
}

/** PKCE S256: the challenge is the SHA-256 of the verifier, base64url-encoded. */
export async function challengeFor(verifier: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  return base64url(new Uint8Array(digest));
}

export function authorizeUrl(
  config: Config,
  redirectUri: string,
  state: string,
  challenge: string,
): string {
  const query = new URLSearchParams({
    response_type: "code",
    client_id: config.clientId,
    redirect_uri: redirectUri,
    scope: "openid email",
    state,
    code_challenge: challenge,
    code_challenge_method: "S256",
  });
  return `https://${config.domain}/oauth2/authorize?${query}`;
}

export async function startSignIn(config: Config, storage: Storage = sessionStorage): Promise<void> {
  const verifier = randomString();
  const state = randomString(16);
  storage.setItem(VERIFIER, verifier);
  storage.setItem(STATE, state);
  window.location.assign(authorizeUrl(config, redirectUri(), state, await challengeFor(verifier)));
}

/** After Cognito redirects back with ?code=...&state=..., trade the code for tokens. */
export async function finishSignIn(
  config: Config,
  url: URL,
  storage: Storage = sessionStorage,
  post: typeof fetch = fetch,
): Promise<Tokens | null> {
  const code = url.searchParams.get("code");
  const state = url.searchParams.get("state");
  const verifier = storage.getItem(VERIFIER);
  if (!code) return null;
  storage.removeItem(VERIFIER);
  if (!state || state !== storage.getItem(STATE) || !verifier) {
    throw new Error("The sign-in didn't come from this page. Please sign in again.");
  }
  storage.removeItem(STATE);
  const tokens = await tokenRequest(config, post, {
    grant_type: "authorization_code",
    client_id: config.clientId,
    code,
    redirect_uri: redirectUri(url),
    code_verifier: verifier,
  });
  storage.setItem(TOKENS, JSON.stringify(tokens));
  return tokens;
}

/** The stored tokens, refreshed if the access token is about to expire. */
export async function currentTokens(
  config: Config,
  storage: Storage = sessionStorage,
  now: number = Date.now(),
  post: typeof fetch = fetch,
): Promise<Tokens | null> {
  const stored = storage.getItem(TOKENS);
  if (!stored) return null;
  const tokens = JSON.parse(stored) as Tokens;
  if (tokens.expiresAt - now > 60_000) return tokens;
  if (!tokens.refresh) return null;
  try {
    const fresh = await tokenRequest(config, post, {
      grant_type: "refresh_token",
      client_id: config.clientId,
      refresh_token: tokens.refresh,
    });
    const kept = { ...fresh, refresh: fresh.refresh ?? tokens.refresh };
    storage.setItem(TOKENS, JSON.stringify(kept));
    return kept;
  } catch {
    storage.removeItem(TOKENS);
    return null;
  }
}

export function signOut(config: Config, storage: Storage = sessionStorage): void {
  storage.removeItem(TOKENS);
  const query = new URLSearchParams({ client_id: config.clientId, logout_uri: redirectUri() });
  window.location.assign(`https://${config.domain}/logout?${query}`);
}

export function redirectUri(url: URL = new URL(window.location.href)): string {
  return `${url.origin}/`;
}

async function tokenRequest(
  config: Config,
  post: typeof fetch,
  form: Record<string, string>,
): Promise<Tokens> {
  const response = await post(`https://${config.domain}/oauth2/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams(form),
  });
  if (!response.ok) throw new Error(`Cognito refused the sign-in (HTTP ${response.status})`);
  const body = (await response.json()) as {
    access_token: string;
    refresh_token?: string;
    expires_in: number;
  };
  return {
    access: body.access_token,
    refresh: body.refresh_token,
    expiresAt: Date.now() + body.expires_in * 1000,
  };
}
