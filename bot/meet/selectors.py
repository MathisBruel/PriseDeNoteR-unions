"""Tous les sélecteurs dépendant de l'interface Google Meet sont regroupés ici.

Meet n'offre pas d'API stable pour son interface : quand Google la modifie, c'est ce fichier
(et lui seul) qu'il faut ajuster. L'interface est forcée en anglais (`?hl=en`) ; les variantes
françaises sont gardées en secours.
"""

import re

# Écran d'accueil de la réunion
JOIN_BUTTON_NAME = re.compile(
    r"^(Join now|Ask to join|Join anyway|Switch here|Participer|Participer à la réunion|"
    r"Participer quand même|Demander à participer)$",
    re.I,
)
MIC_OFF = '[aria-label*="Turn off microphone"], [aria-label*="Désactiver le micro"]'
CAM_OFF = '[aria-label*="Turn off camera"], [aria-label*="Désactiver la caméra"]'
DISMISS_BUTTON_NAME = re.compile(
    r"^(Got it|Dismiss|OK|Continue without microphone and camera|J'ai compris|Ignorer|"
    r"Continuer sans micro ni caméra)$",
    re.I,
)
DENIED_TEXTS = [
    "You can't join this video call",
    "Someone in the call denied your request",
    "No one responded to your request",
    "Vous ne pouvez pas participer à cet appel vidéo",
    "a refusé votre demande",
    "Personne n'a répondu à votre demande",
]
SIGN_IN_HOST = "accounts.google.com"

# En réunion
LEAVE_BUTTON = 'button[aria-label*="Leave call"], button[aria-label*="Quitter l\'appel"]'
CHAT_BUTTON = 'button[aria-label*="Chat with everyone"], button[aria-label*="Discuter avec tous"]'
CHAT_INPUT = 'textarea[aria-label*="Send a message"], textarea[aria-label*="Envoyer un message"]'
CHAT_MESSAGE = "[data-message-id]"
CAPTIONS_BUTTON = 'button[aria-label*="Turn on captions"], button[aria-label*="Activer les sous-titres"]'
CAPTIONS_REGION = '[role="region"][aria-label*="Captions"], [role="region"][aria-label*="Sous-titres"]'
PARTICIPANT_TILE = "[data-participant-id]"
ENDED_TEXTS = [
    "You left the meeting",
    "You've left the call",
    "The call ended",
    "You've been removed from the meeting",
    "Return to home screen",
    "Vous avez quitté la réunion",
    "L'appel est terminé",
    "Vous avez été exclu de la réunion",
    "Revenir à l'écran d'accueil",
]
