# PriseDeNoteRéunions

Bot auto-hébergé qui rejoint les réunions **Google Meet** d'un compte Google Workspace, les enregistre
sur commande tapée dans le chat, capture le transcript en direct et produit par IA un compte rendu
téléchargeable (PDF, Word, Markdown, Google Docs), déposé automatiquement dans un Drive partagé.

## Fonctionnement

1. Le compte Google du bot est invité à une réunion → elle apparaît dans le tableau de bord.
2. Le bot entre dans la réunion (micro et caméra coupés) et s'annonce dans le chat.
3. Dans le chat de la réunion :

   | Commande | Effet |
   |---|---|
   | `!enregistrer` | démarre l'enregistrement vidéo + le transcript (le bot le confirme dans le chat) |
   | `!stop` | arrête l'enregistrement, le bot reste |
   | `!quitter` | arrête l'enregistrement et fait partir le bot |
   | `!statut` | indique si un enregistrement est en cours |

   Préfixe et mots sont modifiables dans les paramètres ; les mêmes actions existent en boutons dans l'admin.
4. Pendant l'enregistrement, l'admin affiche le transcript et un résumé mis à jour en direct.
5. À la fin : retranscription Whisper, compte rendu IA, exports et dépôt dans Drive.

## Installation

Prérequis : Docker + Docker Compose, ~2 vCPU et 2 Go de RAM par réunion simultanée.

```bash
cp .env.example .env
```

Renseignez `APP_SECRET_KEY` (`openssl rand -hex 32`) et `PUBLIC_URL` dans `.env`, puis :

```bash
docker compose up -d --build
```

Ouvrez `http://localhost:8080` et créez le compte administrateur.

### Configuration (page Paramètres)

1. **Compte Google Workspace** — dans [Google Cloud Console](https://console.cloud.google.com/) :
   créez un projet, activez *Google Calendar API* et *Google Drive API*, configurez l'écran de consentement
   en type **Interne**, puis créez un identifiant OAuth « Application Web » avec l'URI de redirection
   affichée dans la page (`<PUBLIC_URL>/api/google/oauth/callback`). Collez identifiant et secret, puis
   « Connecter le compte Google » avec le compte du bot.
2. **Navigateur du bot** — « Ouvrir la fenêtre de connexion », connectez-vous à Google avec le même compte
   dans la fenêtre distante, puis « J'ai terminé ». La session est chiffrée et réutilisée par chaque bot.
   La langue des sous-titres Meet est celle réglée sur ce compte.
3. **Intelligence artificielle** — fournisseur (Claude, OpenAI, Gemini, Mistral, Ollama ou tout endpoint
   compatible OpenAI), modèle et clé API ; bouton « Enregistrer et tester ».
4. **Drive** — choisissez le dossier (Drive partagé ou Mon Drive), les formats et le nommage.

## Architecture

| Service | Rôle |
|---|---|
| `web` | Interface admin React + reverse proxy nginx |
| `api` | API FastAPI (REST + WebSocket) |
| `worker` | Synchro agenda, planification des bots, Whisper, IA, exports (arq) |
| `bot` | Un Chromium isolé par réunion (Xvfb + PulseAudio + ffmpeg), noVNC pour la connexion |
| `postgres`, `redis` | Données, file de tâches et temps réel |

Les fichiers (enregistrements, session du navigateur, modèles Whisper) sont dans `./data`.
Pour personnaliser le Word généré, déposez un gabarit dans `data/templates/report.docx`.

## Développement

```bash
cd backend && pip install -r requirements.txt -r requirements-dev.txt && pytest
```

```bash
pip install -r bot/requirements.txt pytest pytest-asyncio && playwright install chromium && pytest -c bot/pyproject.toml bot/tests
```

```bash
cd frontend && npm install && npm run dev
```

Les tests du bot tournent sur une page Meet factice (`bot/tests/fake_meet.html`), sans accès à Google.

## Limites à connaître

- **Interface Meet** : le bot pilote l'interface web de Meet, que Google peut modifier sans préavis. Tous les
  sélecteurs sont regroupés dans `bot/meet/selectors.py` ; c'est le fichier à ajuster si le bot ne trouve
  plus un bouton.
- **Session Google** : elle peut expirer ; l'admin l'indique et il suffit de refaire l'étape 2.
- **Commandes** : le chat ne donne que le nom affiché des participants ; la restriction « personnes
  autorisées » se fait donc par nom.
- **Consentement** : enregistrer une réunion suppose d'en informer les participants. Le bot l'annonce dans
  le chat ; vérifiez les obligations applicables (RGPD) et réglez la durée de conservation.
- Servez l'interface en HTTPS (reverse proxy) hors usage local, avec `COOKIE_SECURE=true`.
