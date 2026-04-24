from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


load_dotenv()


def _get_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _get_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default

    try:
        return int(value)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer.") from error


@dataclass(frozen=True)
class Settings:
    green_api_id_instance: str
    green_api_token_instance: str
    openai_api_key: str
    openai_model: str
    bot_system_prompt: str
    max_history_messages: int
    ignore_group_chats: bool
    google_apps_script_url: str
    catalog_refresh_seconds: int
    payment_kaspi_qr_url: str
    payment_kaspi_qr_file: str
    payment_transfer_number: str
    payment_other_bank_card: str
    payment_transfer_name: str
    payment_instructions_text: str
    google_apps_script_secret: str
    payment_log_sheet_name: str
    clients_sheet_name: str


def load_settings() -> Settings:
    required = {
        "GREEN_API_ID_INSTANCE": os.getenv("GREEN_API_ID_INSTANCE", "").strip(),
        "GREEN_API_TOKEN_INSTANCE": os.getenv("GREEN_API_TOKEN_INSTANCE", "").strip(),
        "OPENAI_API_KEY": os.getenv("OPENAI_API_KEY", "").strip(),
        "GOOGLE_APPS_SCRIPT_URL": os.getenv("GOOGLE_APPS_SCRIPT_URL", "").strip(),
        "GOOGLE_APPS_SCRIPT_SECRET": os.getenv("GOOGLE_APPS_SCRIPT_SECRET", "").strip(),
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        joined = ", ".join(missing)
        raise ValueError(f"Missing required environment variables: {joined}")

    return Settings(
        green_api_id_instance=required["GREEN_API_ID_INSTANCE"],
        green_api_token_instance=required["GREEN_API_TOKEN_INSTANCE"],
        openai_api_key=required["OPENAI_API_KEY"],
        openai_model=os.getenv("OPENAI_MODEL", "gpt-5.2").strip(),
        bot_system_prompt=os.getenv(
            "BOT_SYSTEM_PROMPT",
            (
                "You are a warm WhatsApp sales specialist for a furniture/beauty-zone product shop. "
                "Speak naturally like a real consultant, not like a FAQ bot. "
                "Answer the customer's exact question first, add one useful benefit, "
                "and guide the customer to one clear next step such as choosing color, city, photo, or order. "
                "If information is missing, ask one short clarifying question. "
                "Use only provided catalog facts for price, colors, stock, delivery, sizes, and guarantees. "
                "Never invent prices, delivery dates, scarcity, or guarantees."
            ),
        ).strip(),
        max_history_messages=_get_int("MAX_HISTORY_MESSAGES", 8),
        ignore_group_chats=_get_bool("IGNORE_GROUP_CHATS", True),
        google_apps_script_url=required["GOOGLE_APPS_SCRIPT_URL"],
        catalog_refresh_seconds=_get_int("CATALOG_REFRESH_SECONDS", 60),
        payment_kaspi_qr_url=os.getenv("PAYMENT_KASPI_QR_URL", "").strip(),
        payment_kaspi_qr_file=os.getenv(
            "PAYMENT_KASPI_QR_FILE", "assets/payment/kaspi_qr.jpg"
        ).strip(),
        payment_transfer_number=os.getenv("PAYMENT_TRANSFER_NUMBER", "").strip(),
        payment_other_bank_card=os.getenv("PAYMENT_OTHER_BANK_CARD", "").strip(),
        payment_transfer_name=os.getenv("PAYMENT_TRANSFER_NAME", "").strip(),
        payment_instructions_text=os.getenv(
            "PAYMENT_INSTRUCTIONS_TEXT",
            "Kaspi QR немесе аударым арқылы төлеуге болады.",
        ).strip(),
        google_apps_script_secret=required["GOOGLE_APPS_SCRIPT_SECRET"],
        payment_log_sheet_name=os.getenv("PAYMENT_LOG_SHEET_NAME", "Payments").strip(),
        clients_sheet_name=os.getenv("CLIENTS_SHEET_NAME", "Clients").strip(),
    )
