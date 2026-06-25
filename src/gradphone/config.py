from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    gradium_api_key: str = field(default_factory=lambda: os.environ["GRADIUM_API_KEY"])
    # No hardcoded default: a real voice UID is account-scoped, so baking one in
    # would ship the original author's clone with every fork. Set it in .env.
    agent_voice_id: str = field(default_factory=lambda: os.environ["AGENT_VOICE_ID"])

    twilio_account_sid: str = field(default_factory=lambda: os.environ["TWILIO_ACCOUNT_SID"])
    twilio_auth_token: str = field(default_factory=lambda: os.environ.get("TWILIO_AUTH_TOKEN", ""))
    twilio_phone_number: str = field(default_factory=lambda: os.environ["TWILIO_PHONE_NUMBER"])


cfg = Config()
