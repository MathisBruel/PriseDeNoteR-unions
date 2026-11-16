import { FormEvent, useEffect, useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import { api, ApiError } from "./api";
import Dashboard from "./pages/Dashboard";
import History from "./pages/History";
import Logs from "./pages/Logs";
import MeetingPage from "./pages/MeetingPage";
import SettingsPage from "./pages/Settings";
import { Notice, useAction } from "./ui";

type Auth = { state: "loading" } | { state: "setup" } | { state: "login" } | { state: "ok"; email: string };

export default function App() {
  const [auth, setAuth] = useState<Auth>({ state: "loading" });

  const check = async () => {
    try {
      const me = await api<{ email: string }>("/auth/me");
      setAuth({ state: "ok", email: me.email });
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        const status = await api<{ needs_setup: boolean }>("/auth/status");
        setAuth({ state: status.needs_setup ? "setup" : "login" });
      }
    }
  };
  useEffect(() => {
    check();
  }, []);

  if (auth.state === "loading") return <div className="center muted">Chargement…</div>;
  if (auth.state !== "ok") return <AuthForm setup={auth.state === "setup"} onDone={check} />;

  const logout = async () => {
    await api("/auth/logout", { method: "POST" });
    setAuth({ state: "login" });
  };

  return (
    <div className="layout">
      <nav className="sidebar">
        <div className="brand">
          <span className="brand-dot" />
          Prise de notes
        </div>
        <NavLink to="/" end>
          Tableau de bord
        </NavLink>
        <NavLink to="/history">Historique</NavLink>
        <NavLink to="/settings">Paramètres</NavLink>
        <NavLink to="/logs">Journaux</NavLink>
        <div className="sidebar-foot">
          <div className="muted small">{auth.email}</div>
          <button className="link" onClick={logout}>
            Se déconnecter
          </button>
        </div>
      </nav>
      <main>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/history" element={<History />} />
          <Route path="/meetings/:id" element={<MeetingPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="/logs" element={<Logs />} />
        </Routes>
      </main>
    </div>
  );
}

function AuthForm({ setup, onDone }: { setup: boolean; onDone: () => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const { busy, error, run } = useAction();

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (await run(() => api(setup ? "/auth/setup" : "/auth/login", { body: { email, password } }))) onDone();
  };

  return (
    <div className="center">
      <form className="card auth" onSubmit={submit}>
        <div className="brand">
          <span className="brand-dot" />
          Prise de notes
        </div>
        <h1>{setup ? "Créer le compte administrateur" : "Connexion"}</h1>
        {setup && <p className="muted">Première utilisation : choisissez les identifiants de l'administrateur.</p>}
        {error && <Notice kind="error">{error}</Notice>}
        <label>
          Adresse e-mail
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoFocus />
        </label>
        <label>
          Mot de passe {setup && <span className="muted">(8 caractères minimum)</span>}
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required minLength={8} />
        </label>
        <button className="primary" disabled={busy}>
          {setup ? "Créer le compte" : "Se connecter"}
        </button>
      </form>
    </div>
  );
}
