import type { Runbook } from "../types.ts";
import { type TokenSource, useApi } from "../useApi.ts";

export function Runbooks({ token }: { token: TokenSource }) {
  const { data, error } = useApi<{ runbooks: Runbook[] }>("runbooks", token);
  if (error) return <p className="error">{error}</p>;
  if (!data) return <p className="muted">Loading…</p>;
  return (
    <section>
      <h1>Runbooks</h1>
      <p className="muted">
        The only fixes NightWatch can run, each after a human approves it in Discord. They're reviewed like code in{" "}
        <code>runbooks/*.yaml</code>.
      </p>
      {data.runbooks.map((book) => (
        <section key={book.id} className="card">
          <h2>
            {book.title} <span className="badge">{book.risk} risk</span>
            {book.auto_approve && <span className="badge warn"> auto-approve</span>}
          </h2>
          <p>{book.description}</p>
          <p className="muted small">
            <code>{book.id}</code> · fixes a <strong>{book.signal.replace(/_/g, " ")}</strong> · calls{" "}
            <code>
              {book.action.service}.{book.action.call}
            </code>
            {book.rollback && (
              <>
                {" "}· undo with{" "}
                <code>
                  {book.rollback.service}.{book.rollback.call}
                </code>
              </>
            )}
          </p>
          <pre>{book.preview}</pre>
          <p className="muted small">
            Verified by {book.verify.namespace} {book.verify.metric} ({book.verify.stat}) staying at or below {book.verify.max_value} for{" "}
            {book.verify.minutes} min.
          </p>
        </section>
      ))}
    </section>
  );
}
