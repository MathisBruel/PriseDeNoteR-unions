import { useEffect, useState } from "react";
import { Meeting } from "../api";
import { Card, MeetingTable, Notice, useFetch } from "../ui";

export default function History() {
  const [query, setQuery] = useState("");
  const [debounced, setDebounced] = useState("");
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(query), 300);
    return () => clearTimeout(timer);
  }, [query]);
  const meetings = useFetch<Meeting[]>(`/meetings?scope=past&q=${encodeURIComponent(debounced)}`, 15000);

  return (
    <>
      <h1>Historique</h1>
      {meetings.error && <Notice kind="error">{meetings.error}</Notice>}
      <Card>
        <input placeholder="Rechercher par titre ou organisateur…" value={query} onChange={(e) => setQuery(e.target.value)} />
        <MeetingTable
          meetings={meetings.data ?? []}
          extra={(m) => (
            <span className="muted small">
              {m.has_report && "Compte rendu"} {m.has_recording && "· Vidéo"}
            </span>
          )}
        />
      </Card>
    </>
  );
}
