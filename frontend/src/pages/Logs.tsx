import { Link } from "react-router-dom";
import { formatDate } from "../api";
import { Card, useFetch } from "../ui";

interface LogEntry {
  id: number;
  meeting_id: number | null;
  level: string;
  message: string;
  created_at: string;
}

export default function Logs() {
  const logs = useFetch<LogEntry[]>("/logs", 10000);
  return (
    <>
      <h1>Journaux</h1>
      <Card>
        {!logs.data?.length && <p className="muted">Aucun événement.</p>}
        <div className="table-wrap">
          <table>
            <tbody>
              {(logs.data ?? []).map((entry) => (
                <tr key={entry.id}>
                  <td className="nowrap muted small">{formatDate(entry.created_at)}</td>
                  <td>
                    <span className={`badge badge-log-${entry.level}`}>{entry.level}</span>
                  </td>
                  <td>{entry.message}</td>
                  <td>{entry.meeting_id && <Link to={`/meetings/${entry.meeting_id}`}>Réunion</Link>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </>
  );
}
