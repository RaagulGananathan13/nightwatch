import { useCallback, useEffect, useState } from "react";

import { api, SignedOut } from "./api.ts";

export type TokenSource = () => Promise<string | null>;

export interface Loaded<T> {
  data: T | null;
  error: string | null;
  signedOut: boolean;
  reload: () => void;
}

/** Fetches an API path once the user has a token; refetches when the path changes. */
export function useApi<T>(path: string, token: TokenSource): Loaded<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [signedOut, setSignedOut] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const reload = useCallback(() => setAttempt((n) => n + 1), []);

  useEffect(() => {
    let current = true;
    setError(null);
    (async () => {
      const access = await token();
      if (!access) throw new SignedOut();
      return api<T>(path, access);
    })()
      .then((value) => current && setData(value))
      .catch((problem: unknown) => {
        if (!current) return;
        if (problem instanceof SignedOut) setSignedOut(true);
        setError(problem instanceof Error ? problem.message : String(problem));
      });
    return () => {
      current = false;
    };
  }, [path, token, attempt]);

  return { data, error, signedOut, reload };
}
