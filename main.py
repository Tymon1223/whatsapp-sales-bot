from __future__ import annotations

import logging
import os
from pathlib import Path
from threading import Event, Thread

from whatsapp_chatbot_python import GreenAPIBot

from app.handlers import (
    handle_clear,
    handle_manual_outgoing,
    handle_receipt_notification,
    handle_start,
    handle_text_notification,
)
from app.runtime import create_runtime


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("whatsapp-ai-bot")
PROJECT_ROOT = Path(__file__).resolve().parent


def _clear_broken_proxy_settings() -> None:
    for env_name in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "GIT_HTTP_PROXY",
        "GIT_HTTPS_PROXY",
    ):
        value = os.environ.get(env_name, "")
        if "127.0.0.1:9" in value:
            os.environ.pop(env_name, None)
            logger.info("Removed broken proxy setting: %s", env_name)


_clear_broken_proxy_settings()
runtime = create_runtime(PROJECT_ROOT)
bot = GreenAPIBot(
    runtime.settings.green_api_id_instance,
    runtime.settings.green_api_token_instance,
)
follow_up_stop_event = Event()


@bot.router.message(command="start")
def start_handler(notification) -> None:
    handle_start(runtime, notification)


@bot.router.message(command="clear")
def clear_handler(notification) -> None:
    handle_clear(runtime, notification)


@bot.router.message(
    type_message=[
        "textMessage",
        "extendedTextMessage",
        "templateButtonsReplyMessage",
        "interactiveButtonsReply",
        "interactiveButtonReply",
        "interactiveButtonsResponse",
        "buttonsResponseMessage",
        "listResponseMessage",
    ]
)
def text_handler(notification) -> None:
    handle_text_notification(runtime, notification)


@bot.router.message(type_message=["imageMessage", "documentMessage"])
def receipt_handler(notification) -> None:
    handle_receipt_notification(runtime, notification)


@bot.router.outgoing_message(type_message=["textMessage", "extendedTextMessage"])
def outgoing_manager_handler(notification) -> None:
    handle_manual_outgoing(runtime, notification)


def _run_follow_up_worker() -> None:
    while not follow_up_stop_event.wait(30):
        for chat_id, scheduled in runtime.sales_flow.pop_due_follow_ups():
            if runtime.sales_flow.is_manager_mode(chat_id):
                logger.info("Skipping follow-up for %s because manager mode is active", chat_id)
                continue

            try:
                bot.api.sending.sendMessage(chat_id, scheduled.reminder_text)
                runtime.ai_service.record_assistant_message(chat_id, scheduled.reminder_text)
                logger.info("Follow-up reminder sent to %s", chat_id)
            except Exception:
                logger.exception("Failed to send follow-up reminder to %s", chat_id)


def main() -> None:
    logger.info("Bot is starting")
    worker = Thread(target=_run_follow_up_worker, name="follow-up-worker", daemon=True)
    worker.start()
    try:
        bot.run_forever()
    finally:
        follow_up_stop_event.set()


if __name__ == "__main__":
    main()
