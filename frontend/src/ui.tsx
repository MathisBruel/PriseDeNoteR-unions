import { ReactNode, useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, formatDate, Meeting, STATUS_LABELS } from "./api";

export function StatusBadge({ status }: { status: string }) {
  return <span className={`badge badge-${status}`}>{STATUS_LABELS[status] ?? status}</span>;
}

export function Card({ title, actions, children }: { title?: string; actions?: ReactNode; children: ReactNode }) {
  return (
    <section className="card">
      {(title || actions) && (
        <header className="card-header">
          <h2>{title}</h2>
          <div className="row">{actions}</div>
        </header>
      )}
      {children}
    </section>
  );
}

export function Notice({ kind = "info", children }: { kind?: "info" | "error" | "success" | "warning"; children: ReactNode }) {
  return <div className={`notice notice-${kind}`}>{children}</div>;
}

/** Charge une ressource, avec rechargement manuel et périodique optionnel. */
export function useFetch<T>(path: string | null, refreshMs = 0) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  const reload = useCallback(async () => {
    if (!path) return;
    try {
      setData(await api<T>(path));
      setError("");
    } catch (e) {
      setError((e as Error).message);
    }
  }, [path]);
  useEffect(() => {
    reload();
    if (!refreshMs) return;
    const timer = setInterval(reload, refreshMs);
    return () => clearInterval(timer);
  }, [reload, refreshMs]);
  return { data, error, reload, setData };
}

/** Exécute une action asynchrone en exposant l'état « en cours » et l'erreur éventuelle. */
export function useAction() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError("");
    try {
      await fn();
      return true;
    } catch (e) {
      setError((e as Error).message);
      return false;
    } finally {
      setBusy(false);
    }
  };
  return { busy, error, run };
}

export function MeetingTable({ meetings, extra }: { meetings: Meeting[]; extra?: (m: Meeting) => ReactNode }) {
  if (!meetings.length) return <p className="muted">Aucune réunion.</p>;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Réunion</th>
            <th>Date</th>
            <th>Statut</th>
            {extra && <th></th>}
          </tr>
        </thead>
        <tbody>
          {meetings.map((m) => (
            <tr key={m.id}>
              <td>
                <Link to={`/meetings/${m.id}`} className="strong">
                  {m.title}
                </Link>
                {m.organizer && <div className="muted small">{m.organizer}</div>}
              </td>
              <td className="nowrap">{formatDate(m.start_at)}</td>
              <td>
                <StatusBadge status={m.status} />
                {m.status === "failed" && m.status_message && <div className="muted small">{m.status_message}</div>}
              </td>
              {extra && <td className="actions">{extra(m)}</td>}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
