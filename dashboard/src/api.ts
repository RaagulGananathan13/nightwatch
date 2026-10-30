// Calls to the dashboard API. CloudFront routes /api/ to the API Lambda, so they are
// same-origin requests. The access token travels in X-NightWatch-Token, because CloudFront's
// own signature to the Lambda uses the Authorization header.

export class SignedOut extends Error {
  constructor() {
    super("Your session has ended. Please sign in again.");
  }
}

export async function api<T>(path: string, token: string): Promise<T> {
  const response = await fetch(`/api/${path}`, { headers: { "X-NightWatch-Token": token } });
  if (response.status === 401) throw new SignedOut();
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as { error?: string };
    throw new Error(body.error ?? `The API answered HTTP ${response.status}`);
  }
  return (await response.json()) as T;
}
