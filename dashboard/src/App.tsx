import { useCallback, useEffect, useState } from "react";

import { type Config, currentTokens, finishSignIn, signOut, startSignIn } from "./auth.ts";
import { Eval } from "./pages/Eval.tsx";
import { Incident } from "./pages/Incident.tsx";
import { Incidents } from "./pages/Incidents.tsx";
import { Runbooks } from "./pages/Runbooks.tsx";
import { Settings } from "./pages/Settings.tsx";
import { href, useRoute } from "./router.ts";

const RETURN_TO = "nightwatch.return";

export function App() {
  const [config, setConfig] = useState<Config | null>(null);
  const [signedIn, setSignedIn] = useState<boolean | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const route = useRoute();

  useEffect(() => {
    (async () => {
      const response = await fetch("/config.json", { cache: "no-store" });
      if (!response.ok) throw new Error("This dashboard isn't set up yet (no config.json).");
      const loaded = (await response.json()) as Config;
      setConfig(loaded);
      const url = new URL(window.location.href);
      if (await finishSignIn(loaded, url)) {
        // Drop ?code=... from the address bar and go back where the user was.
        window.history.replaceState(null, "", `${url.origin}/${sessionStorage.getItem(RETURN_TO) ?? ""}`);
        sessionStorage.removeItem(RETURN_TO);
        window.dispatchEvent(new HashChangeEvent("hashchange"));
      }
      setSignedIn(Boolean(await currentTokens(loaded)));
    })().catch((error: unknown) => setProblem(error instanceof Error ? error.message : String(error)));
  }, []);

  const token = useCallback(async () => (config ? (await currentTokens(config))?.access ?? null : null), [config]);
  const signIn = useCallback(() => {
    if (!config) return;
    sessionStorage.setItem(RETURN_TO, window.location.hash);
    void startSignIn(config);
  }, [config]);

  if (problem) return <Centered title="NightWatch" text={problem} />;
  if (!config || signedIn === null) return <Centered title="NightWatch" text="Loading…" />;
  if (!signedIn) {
    return (
      <Centered title="NightWatch" text="Incidents, evidence and postmortems for your on-call team.">
        <button className="primary" onClick={signIn}>
          Sign in
        </button>
      </Centered>
    );
  }

  const [page = "incidents", id, focus] = route;
  return (
    <>
      <header>
        <a className="brand" href={href("incidents")}>
          🌙 NightWatch
        </a>
        <nav>
          {[
            ["incidents", "Incidents"],
            ["eval", "Evaluation"],
            ["runbooks", "Runbooks"],
            ["settings", "Settings"],
          ].map(([name, text]) => (
            <a key={name} href={href(name ?? "")} className={page === name ? "active" : ""}>
              {text}
            </a>
          ))}
        </nav>
        <button className="link" onClick={() => signOut(config)}>
          Sign out
        </button>
      </header>
      <main>
        {page === "incidents" && id && <Incident id={id} focus={focus} token={token} />}
        {page === "incidents" && !id && <Incidents token={token} />}
        {page === "eval" && <Eval />}
        {page === "runbooks" && <Runbooks token={token} />}
        {page === "settings" && <Settings token={token} />}
      </main>
    </>
  );
}

function Centered({ title, text, children }: { title: string; text: string; children?: React.ReactNode }) {
  return (
    <div className="centered">
      <h1>🌙 {title}</h1>
      <p className="muted">{text}</p>
      {children}
    </div>
  );
}
