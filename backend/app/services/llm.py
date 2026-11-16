"""Appels IA via LiteLLM (Claude, OpenAI, Gemini, Mistral, Ollama, endpoint compatible OpenAI)."""

import json
import re

from app.schemas import LLMSettings, ReportData

PROVIDER_PREFIX = {
    "anthropic": "anthropic",
    "openai": "openai",
    "gemini": "gemini",
    "mistral": "mistral",
    "ollama": "ollama",
    "openai_compatible": "openai",
}
CHUNK_CHARS = 60_000

REPORT_SCHEMA = """{
  "title": "titre court de la réunion",
  "summary": "résumé exécutif en un ou deux paragraphes",
  "participants": ["nom"],
  "topics": [{"title": "sujet abordé", "points": ["point clé"]}],
  "decisions": ["décision prise"],
  "action_items": [{"task": "action à mener", "owner": "responsable ou vide", "due": "échéance ou vide"}],
  "open_questions": ["question restée ouverte"]
}"""


class LLMError(Exception):
    pass


def model_name(settings: LLMSettings) -> str:
    prefix = PROVIDER_PREFIX.get(settings.provider, settings.provider)
    return settings.model if settings.model.startswith(f"{prefix}/") else f"{prefix}/{settings.model}"


async def complete(settings: LLMSettings, system: str, user: str, max_tokens: int = 4096) -> str:
    import litellm  # import tardif : dépendance lourde

    kwargs: dict = {
        "model": model_name(settings),
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "max_tokens": max_tokens,
    }
    if settings.api_key:
        kwargs["api_key"] = settings.api_key
    if settings.api_base:
        kwargs["api_base"] = settings.api_base
    elif settings.provider == "ollama":
        kwargs["api_base"] = "http://ollama:11434"
    try:
        response = await litellm.acompletion(**kwargs)
    except Exception as exc:  # LiteLLM lève des exceptions propres à chaque fournisseur
        raise LLMError(str(exc)) from exc
    return response.choices[0].message.content or ""


def parse_json_object(text: str) -> dict:
    """Extrait le premier objet JSON d'une réponse (tolère les blocs ```json et le texte autour)."""
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    candidate = fenced.group(1) if fenced else text[text.find("{") : text.rfind("}") + 1]
    if not candidate:
        raise LLMError("La réponse de l'IA ne contient pas de JSON")
    try:
        return json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise LLMError(f"JSON invalide dans la réponse de l'IA : {exc}") from exc


def split_chunks(text: str, size: int = CHUNK_CHARS) -> list[str]:
    chunks, current, length = [], [], 0
    for line in text.splitlines():
        if length + len(line) > size and current:
            chunks.append("\n".join(current))
            current, length = [], 0
        current.append(line)
        length += len(line) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks


def _system(settings: LLMSettings) -> str:
    extra = f"\nConsignes supplémentaires : {settings.extra_instructions}" if settings.extra_instructions else ""
    return (
        "Tu es un assistant qui rédige des comptes rendus de réunion fidèles, précis et structurés. "
        f"Tu rédiges en {settings.report_language}. Tu n'inventes rien : tout doit provenir du transcript. "
        "Le transcript est issu d'une reconnaissance vocale et peut contenir des erreurs ; corrige les "
        "évidences sans extrapoler." + extra
    )


async def generate_report(settings: LLMSettings, meta: dict, transcript: str, complete_fn=complete) -> ReportData:
    """Compte rendu structuré ; les longues réunions passent par un découpage puis une synthèse."""
    system = _system(settings)
    source_label = "Transcript"
    chunks = split_chunks(transcript)
    if len(chunks) > 1:
        notes = []
        for index, chunk in enumerate(chunks, 1):
            notes.append(
                await complete_fn(
                    settings,
                    system,
                    f"Voici la partie {index}/{len(chunks)} du transcript d'une réunion. Rédige des notes "
                    "détaillées : sujets, arguments, décisions, actions (avec responsables et échéances), "
                    f"questions ouvertes.\n\n{chunk}",
                )
            )
        transcript = "\n\n".join(f"## Partie {i}\n{n}" for i, n in enumerate(notes, 1))
        source_label = "Notes détaillées par partie"
    prompt = (
        f"Réunion : {meta.get('title', '')}\nDate : {meta.get('date', '')}\n"
        f"Invités : {', '.join(meta.get('attendees', [])) or 'inconnus'}\n\n"
        f"{source_label} :\n{transcript}\n\n"
        "Produis le compte rendu de cette réunion. Réponds UNIQUEMENT avec un objet JSON valide "
        f"respectant exactement cette structure :\n{REPORT_SCHEMA}"
    )
    raw = await complete_fn(settings, system, prompt, 8192)
    return ReportData.model_validate(parse_json_object(raw))


async def update_live_summary(settings: LLMSettings, previous: str, new_text: str, complete_fn=complete) -> str:
    prompt = (
        "Réunion en cours. Mets à jour le résumé ci-dessous avec les nouveaux échanges. Réponds "
        "uniquement avec le résumé mis à jour, sous forme de liste à puces concise (10 puces maximum), "
        "en signalant les décisions et actions.\n\n"
        f"Résumé actuel :\n{previous or '(aucun)'}\n\nNouveaux échanges :\n{new_text}"
    )
    return (await complete_fn(settings, _system(settings), prompt, 1024)).strip()
