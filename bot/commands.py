"""Analyse des commandes tapées dans le chat de la réunion."""

import unicodedata

RECORD, STOP, LEAVE, STATUS = "record", "stop", "leave", "status"
SELF_NAMES = {"you", "vous"}


def normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold().strip()


def parse_command(text: str, cfg: dict) -> str | None:
    """Renvoie l'action demandée (record/stop/leave/status) ou None.

    `cfg` porte les réglages de l'admin : command_prefix, cmd_record, cmd_stop, cmd_leave, cmd_status.
    """
    text = text.strip()
    prefix = cfg.get("command_prefix", "!")
    if not prefix or not text.startswith(prefix):
        return None
    words = text[len(prefix):].split()
    if not words:
        return None
    word = normalize(words[0])
    mapping = {
        normalize(cfg.get("cmd_record", "enregistrer")): RECORD,
        normalize(cfg.get("cmd_stop", "stop")): STOP,
        normalize(cfg.get("cmd_leave", "quitter")): LEAVE,
        normalize(cfg.get("cmd_status", "statut")): STATUS,
    }
    return mapping.get(word)


def is_allowed(sender: str, allowed: list[str]) -> bool:
    """Liste vide = tout participant peut piloter le bot."""
    if not allowed:
        return True
    return normalize(sender) in {normalize(name) for name in allowed}


def is_self(sender: str) -> bool:
    return normalize(sender) in SELF_NAMES


def messages(cfg: dict) -> dict:
    p = cfg.get("command_prefix", "!")
    rec, stop, leave = (p + cfg.get(k, d) for k, d in (
        ("cmd_record", "enregistrer"), ("cmd_stop", "stop"), ("cmd_leave", "quitter")))
    return {
        "welcome": f"Bonjour, je suis l'assistant de prise de notes. Écrivez {rec} pour démarrer "
                   f"l'enregistrement, {stop} pour l'arrêter et {leave} pour que je quitte la réunion.",
        "record": f"🔴 Enregistrement démarré : cette réunion est enregistrée et transcrite. "
                  f"({stop} pour arrêter, {leave} pour me faire partir)",
        "already": "L'enregistrement est déjà en cours.",
        "stop": f"⏹️ Enregistrement arrêté. ({rec} pour reprendre)",
        "not_recording": "Aucun enregistrement en cours.",
        "leave": "👋 Je quitte la réunion. Le compte rendu sera disponible sous peu.",
        "leave_plain": "👋 Je quitte la réunion.",
        "status_on": "🔴 Enregistrement en cours depuis {elapsed}.",
        "status_off": f"Aucun enregistrement en cours. ({rec} pour démarrer)",
        "denied": "Désolé, vous n'êtes pas autorisé à me piloter.",
    }
