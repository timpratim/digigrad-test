"""TwiML builders for the voicemail branch.

When Twilio's ``AnsweredBy`` indicates a non-human answerer, ``/twilio/voice``
returns one of these TwiMLs instead of opening the Media Streams WS.
Avoids spending Gradium STT/TTS quota on an unanswerable conversation.

Default behaviour: hang up silently. Some businesses are uneasy about AI
voicemails. Set ``ENABLE_VOICEMAIL_MESSAGE=true`` to opt in to leaving a
brief Twilio-Say message and hanging up.
"""

from __future__ import annotations

import os
from xml.sax.saxutils import escape

_TWILIO_VOICE = {
    "en": ("Polly.Joanna-Neural", "en-US"),
    "fr": ("Polly.Lea-Neural", "fr-FR"),
    "pt": ("Polly.Camila-Neural", "pt-BR"),
}

_VOICEMAIL_MSG = {
    "en": (
        "Hello, I'm calling on behalf of {operator}. "
        "I was hoping to ask about: {task}. "
        "Please call back when you have a moment. Thank you."
    ),
    "fr": (
        "Bonjour, j'appelle de la part de {operator}. "
        "J'aurais voulu vous demander : {task}. "
        "Pourriez-vous me rappeler quand vous aurez un moment ? Merci."
    ),
    "pt": (
        "Olá, estou ligando em nome de {operator}. "
        "Gostaria de perguntar sobre: {task}. "
        "Por favor, retorne a chamada quando puder. Obrigado."
    ),
}

_SILENT_HANGUP = '<?xml version="1.0" encoding="UTF-8"?><Response><Hangup/></Response>'


def is_machine(answered_by: str) -> bool:
    """True if Twilio's AnsweredBy indicates a non-human answerer."""
    if not answered_by:
        return False
    return answered_by.startswith("machine") or answered_by == "fax"


def voicemail_twiml(task: str, language: str = "en") -> str:
    """Return TwiML for an answering-machine pickup.

    Default: silent hangup. Set ``ENABLE_VOICEMAIL_MESSAGE=true`` to leave
    a brief localized message via Twilio Say (no Gradium TTS round-trip).
    """
    enabled = os.environ.get("ENABLE_VOICEMAIL_MESSAGE", "false").lower() in ("true", "1", "yes", "on")
    if not enabled:
        return _SILENT_HANGUP

    operator = os.environ.get("OPERATOR_NAME", "a colleague")
    code = (language or "en").lower()
    if code not in _VOICEMAIL_MSG:
        code = "en"
    voice, lang_tag = _TWILIO_VOICE[code]
    msg_text = _VOICEMAIL_MSG[code].format(
        operator=escape(operator),
        task=escape(task or "a quick question"),
    )
    msg_escaped = escape(msg_text)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Response>'
        '<Pause length="2"/>'
        f'<Say voice="{voice}" language="{lang_tag}">{msg_escaped}</Say>'
        '<Hangup/>'
        '</Response>'
    )
