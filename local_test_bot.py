from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.catalog_service import Product
from app.handlers import (
    handle_clear,
    handle_receipt_notification,
    handle_start,
    handle_text_notification,
)
from app.runtime import BotRuntime, create_runtime


runtime: BotRuntime


@dataclass
class CapturedMessage:
    kind: str
    payload: dict[str, Any]


class _FakeSendingAPI:
    def __init__(self, outbox: list[CapturedMessage]) -> None:
        self._outbox = outbox

    def sendFileByUrl(
        self,
        chat_id: str,
        url_file: str,
        file_name: str,
        caption: str | None = None,
    ) -> None:
        self._outbox.append(
            CapturedMessage(
                kind="file_by_url",
                payload={
                    "chat_id": chat_id,
                    "url": url_file,
                    "file_name": file_name,
                    "caption": caption or "",
                },
            )
        )


class _FakeAPI:
    def __init__(self, outbox: list[CapturedMessage]) -> None:
        self.sending = _FakeSendingAPI(outbox)


class FakeNotification:
    def __init__(self, chat: str, event: dict[str, Any], outbox: list[CapturedMessage]) -> None:
        self.chat = chat
        self.event = event
        self.api = _FakeAPI(outbox)
        self._outbox = outbox

    def answer(self, text: str) -> None:
        self._outbox.append(CapturedMessage(kind="text", payload={"text": text}))

    def answer_with_file(
        self,
        path: str,
        file_name: str | None = None,
        caption: str | None = None,
    ) -> None:
        self._outbox.append(
            CapturedMessage(
                kind="file",
                payload={
                    "path": path,
                    "file_name": file_name or "",
                    "caption": caption or "",
                },
            )
        )

    def answer_with_interactive_buttons_reply(
        self,
        body: str,
        buttons: list[dict[str, str]],
        footer: str | None = None,
        header: str | None = None,
    ) -> None:
        self._outbox.append(
            CapturedMessage(
                kind="buttons",
                payload={
                    "header": header or "",
                    "body": body,
                    "footer": footer or "",
                    "buttons": buttons,
                },
            )
        )


def _build_text_event(sender_name: str, text: str) -> dict[str, Any]:
    return {
        "typeWebhook": "incomingMessageReceived",
        "senderData": {
            "senderName": sender_name,
            "chatName": sender_name,
        },
        "messageData": {
            "typeMessage": "textMessage",
            "textMessageData": {
                "textMessage": text,
            },
        },
    }


def _build_receipt_event(sender_name: str, caption: str) -> dict[str, Any]:
    return {
        "typeWebhook": "incomingMessageReceived",
        "senderData": {
            "senderName": sender_name,
            "chatName": sender_name,
        },
        "messageData": {
            "typeMessage": "imageMessage",
            "imageMessageData": {
                "downloadUrl": "https://example.com/fake-receipt.jpg",
                "caption": caption,
                "fileName": "receipt.jpg",
                "mimeType": "image/jpeg",
            },
        },
    }


def _install_mock_catalog() -> None:
    sample_product = Product(
        name="Тедди матадан туалетный столик",
        price="100 000 тг",
        regular_price="150 000 тг",
        kaspi_installment="125 000 тг",
        colors="ақ, қара, бежевый, қызғылт, сұр",
        description="Жұмсақ қаптамалы, айнасы бар модель.",
        delivery_fee="10 000 тг",
        delivery_time="2-5 күн",
    )

    def find_best_product(query: str) -> Product | None:
        normalized = query.lower()
        keywords = ("столик", "туалет", "тедди", "стол", "айна", "зеркало")
        return sample_product if any(keyword in normalized for keyword in keywords) else None

    runtime.catalog_service.find_best_product = find_best_product
    runtime.catalog_service.find_product_by_name = (
        lambda name: sample_product if name == sample_product.name else None
    )
    runtime.catalog_service.get_default_product = lambda: sample_product
    runtime.catalog_service.get_catalog_text = (
        lambda query: "Catalog status: available.\n- Тедди матадан туалетный столик | price: 100 000 тг"
    )


def _install_mock_ai() -> None:
    def generate_reply(
        chat_id: str,
        user_name: str,
        message_text: str,
        catalog_text: str,
    ) -> str:
        normalized = message_text.lower()
        if "цвет" in normalized:
            return "Сейчас доступны цвета: белый, черный, бежевый, розовый, серый."
        if "түсі" in normalized or "түс" in normalized:
            return "Қазір бар түстер: ақ, қара, бежевый, қызғылт, сұр."
        if any(word in normalized for word in ("здравствуйте", "привет", "добрый")):
            return "Здравствуйте! Напишите, пожалуйста, какой товар вас интересует."
        return f"{user_name}, сұрағыңызды алдым. Нақты тауар атауын жазыңыз, мен бағасын айтамын."

    runtime.ai_service.generate_reply = generate_reply


def _install_mock_payment_logger() -> None:
    def log_receipt(
        chat_id: str,
        whatsapp_name: str,
        customer_full_name: str,
        delivery_address: str,
        product: Product | None,
        receipt_info: dict[str, str],
    ) -> None:
        return None

    runtime.payment_logger.log_receipt = log_receipt


def _print_outbox(outbox: list[CapturedMessage]) -> None:
    if not outbox:
        print("BOT: no reply")
        return

    for item in outbox:
        if item.kind == "text":
            print(f"BOT TEXT:\n{item.payload['text']}")
        elif item.kind == "buttons":
            labels = " | ".join(button["buttonText"] for button in item.payload["buttons"])
            print(f"BOT BUTTON BODY:\n{item.payload['body']}")
            if item.payload["footer"]:
                print(f"BOT BUTTON FOOTER:\n{item.payload['footer']}")
            print(f"BOT BUTTONS: {labels}")
        elif item.kind == "file":
            print(
                "BOT FILE:\n"
                f"path={item.payload['path']}\n"
                f"file_name={item.payload['file_name']}\n"
                f"caption={item.payload['caption']}"
            )
        elif item.kind == "file_by_url":
            print(
                "BOT FILE BY URL:\n"
                f"url={item.payload['url']}\n"
                f"file_name={item.payload['file_name']}\n"
                f"caption={item.payload['caption']}"
            )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Local dry-run tester for the WhatsApp bot without sending messages to WhatsApp."
    )
    parser.add_argument("--chat-id", default="local-test@c.us")
    parser.add_argument("--sender-name", default="Local Tester")
    parser.add_argument("--mock-catalog", action="store_true")
    parser.add_argument("--mock-ai", action="store_true")
    parser.add_argument("--mock-payment-log", action="store_true")
    args = parser.parse_args()

    global runtime
    runtime = create_runtime(Path(__file__).resolve().parent)

    if args.mock_catalog:
        _install_mock_catalog()
    if args.mock_ai:
        _install_mock_ai()
    if args.mock_payment_log:
        _install_mock_payment_logger()

    print("Local dry-run mode started.")
    print("Commands:")
    print("  /start")
    print("  /clear")
    print("  /receipt Аты-жөні: ...  Адрес: ...")
    print("  /quit")

    while True:
        try:
            incoming = input("\nCLIENT> ").lstrip("\ufeff").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nStopped.")
            return 0

        if not incoming:
            continue

        if incoming == "/quit":
            print("Stopped.")
            return 0

        outbox: list[CapturedMessage] = []

        if incoming == "/start":
            notification = FakeNotification(
                chat=args.chat_id,
                event=_build_text_event(args.sender_name, incoming),
                outbox=outbox,
            )
            handle_start(runtime, notification)
            _print_outbox(outbox)
            continue

        if incoming == "/clear":
            notification = FakeNotification(
                chat=args.chat_id,
                event=_build_text_event(args.sender_name, incoming),
                outbox=outbox,
            )
            handle_clear(runtime, notification)
            _print_outbox(outbox)
            continue

        if incoming.startswith("/receipt"):
            caption = incoming[len("/receipt") :].strip()
            notification = FakeNotification(
                chat=args.chat_id,
                event=_build_receipt_event(args.sender_name, caption),
                outbox=outbox,
            )
            handle_receipt_notification(runtime, notification)
            _print_outbox(outbox)
            continue

        notification = FakeNotification(
            chat=args.chat_id,
            event=_build_text_event(args.sender_name, incoming),
            outbox=outbox,
        )
        handle_text_notification(runtime, notification)
        _print_outbox(outbox)


if __name__ == "__main__":
    raise SystemExit(main())
