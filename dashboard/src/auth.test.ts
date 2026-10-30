import assert from "node:assert/strict";
import { test } from "node:test";

import { authorizeUrl, base64url, challengeFor, currentTokens, finishSignIn, type Config } from "./auth.ts";

const config: Config = {
  region: "us-east-2",
  userPoolId: "us-east-2_test",
  clientId: "client123",
  domain: "nightwatch-1.auth.us-east-2.amazoncognito.com",
};

class MemoryStorage implements Storage {
  private items = new Map<string, string>();
  get length() {
    return this.items.size;
  }
  clear() {
    this.items.clear();
  }
  getItem(key: string) {
    return this.items.get(key) ?? null;
  }
  key(index: number) {
    return [...this.items.keys()][index] ?? null;
  }
  removeItem(key: string) {
    this.items.delete(key);
  }
  setItem(key: string, value: string) {
    this.items.set(key, value);
  }
}

function tokenEndpoint(reply: object, seen: URLSearchParams[] = []): typeof fetch {
  return (async (_url: string | URL | Request, init?: RequestInit) => {
    seen.push(new URLSearchParams(String(init?.body)));
    return new Response(JSON.stringify(reply), { status: 200 });
  }) as typeof fetch;
}

test("PKCE challenge is the RFC 7636 example", async () => {
  // RFC 7636 appendix B: this verifier must give this challenge.
  const challenge = await challengeFor("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk");
  assert.equal(challenge, "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM");
  assert.equal(base64url(new Uint8Array([251, 255])), "-_8");
});

test("the authorize URL asks for a code with S256 PKCE", () => {
  const url = new URL(authorizeUrl(config, "https://d1.cloudfront.net/", "st", "ch"));
  assert.equal(url.host, config.domain);
  assert.equal(url.pathname, "/oauth2/authorize");
  assert.equal(url.searchParams.get("response_type"), "code");
  assert.equal(url.searchParams.get("code_challenge_method"), "S256");
  assert.equal(url.searchParams.get("redirect_uri"), "https://d1.cloudfront.net/");
});

test("a returned code becomes tokens only when the state matches", async () => {
  const storage = new MemoryStorage();
  storage.setItem("nightwatch.verifier", "v");
  storage.setItem("nightwatch.state", "good");
  const seen: URLSearchParams[] = [];
  const reply = { access_token: "a", refresh_token: "r", expires_in: 3600 };
  const url = new URL("https://d1.cloudfront.net/?code=c&state=good");
  const tokens = await finishSignIn(config, url, storage, tokenEndpoint(reply, seen));
  assert.equal(tokens?.access, "a");
  assert.equal(seen[0]?.get("code_verifier"), "v");
  assert.equal(seen[0]?.get("grant_type"), "authorization_code");
  assert.equal(storage.getItem("nightwatch.verifier"), null);

  storage.setItem("nightwatch.verifier", "v");
  storage.setItem("nightwatch.state", "good");
  const forged = new URL("https://d1.cloudfront.net/?code=c&state=evil");
  await assert.rejects(finishSignIn(config, forged, storage, tokenEndpoint(reply)), /didn't come from this page/);
  assert.equal(await finishSignIn(config, new URL("https://d1.cloudfront.net/"), storage), null);
});

test("expiring tokens are refreshed, and a failed refresh signs out", async () => {
  const storage = new MemoryStorage();
  const now = 1_000_000;
  storage.setItem("nightwatch.tokens", JSON.stringify({ access: "old", refresh: "r", expiresAt: now + 30_000 }));
  const fresh = await currentTokens(config, storage, now, tokenEndpoint({ access_token: "new", expires_in: 3600 }));
  assert.equal(fresh?.access, "new");
  assert.equal(fresh?.refresh, "r");

  storage.setItem("nightwatch.tokens", JSON.stringify({ access: "old", refresh: "r", expiresAt: now }));
  const failing = (async () => new Response("{}", { status: 400 })) as typeof fetch;
  assert.equal(await currentTokens(config, storage, now, failing), null);
  assert.equal(storage.getItem("nightwatch.tokens"), null);
});
