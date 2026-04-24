from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
import json
from pathlib import Path
import re
from threading import Lock

from openai import OpenAI

from app.config import Settings


@dataclass
class Message:
    role: str
    content: str


@dataclass
class RouterDecision:
    action: str
    reply_text: str = ""
    product_name: str = ""
    color: str = ""
    quantity: str = ""
    customer_full_name: str = ""
    customer_phone: str = ""
    delivery_address: str = ""
    payment_method: str = ""
    reminder_text: str = ""
    follow_up_delay_minutes: int = 0


STYLE_RULES = """
Response style rules for WhatsApp:
- Reply in plain text only. Do not use markdown, asterisks, bold, underscores, or bullet symbols.
- Reply in the customer's language when it is clearly Russian or Kazakh. If the language is mixed, use natural Russian with simple Kazakh-friendly phrasing.
- Keep the reply human, warm, and consultative, usually 2 to 5 short lines.
- Sound like a real sales specialist: listen first, answer the exact question, then gently guide to the next step.
- For product questions, use this natural sales flow:
  1. direct answer to the customer's question
  2. one relevant benefit tied to the product or customer's situation
  3. current price, old price, installment, delivery, or color only when relevant
  4. one short closing question such as color, city, photo, or order step
- If the customer is comparing, hesitating, or asking "дорого", acknowledge it calmly and explain value without pressure.
- If the customer asks for price, include price and one benefit, then ask whether to send photos/video or help choose color.
- If the customer asks for photos/video/color, confirm and keep the caption short.
- Do not ask several questions at once. Ask only one next-step question.
- Do not sound robotic, generic, or like a FAQ template.
- Avoid pressure, fake scarcity, fake guarantees, and invented discounts.
- Do not say "catalog says" or "according to the catalog".
- Do not copy raw field labels like "sale price" or "regular price" into the customer-facing reply.
- Use only catalog facts for prices, stock, colors, delivery, sizes, and guarantees.
""".strip()


class OpenAIService:
    def __init__(self, settings: Settings, history_store_path: Path | None = None) -> None:
        self._settings = settings
        self._client = OpenAI(api_key=settings.openai_api_key)
        self._history: dict[str, deque[Message]] = defaultdict(
            lambda: deque(maxlen=settings.max_history_messages * 2)
        )
        self._lock = Lock()
        self._history_store_path = history_store_path
        self._load_history_store()

    def _load_history_store(self) -> None:
        if not self._history_store_path or not self._history_store_path.exists():
            return

        try:
            payload = json.loads(self._history_store_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return

        if not isinstance(payload, dict):
            return

        for chat_id, items in payload.items():
            if not isinstance(items, list):
                continue
            history = deque(maxlen=self._settings.max_history_messages * 2)
            for item in items:
                if not isinstance(item, dict):
                    continue
                role = str(item.get("role", "")).strip()
                content = str(item.get("content", "")).strip()
                if role and content:
                    history.append(Message(role=role, content=content))
            if history:
                self._history[str(chat_id)] = history

    def _save_history_store(self) -> None:
        if not self._history_store_path:
            return

        try:
            self._history_store_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                chat_id: [{"role": msg.role, "content": msg.content} for msg in list(messages)]
                for chat_id, messages in self._history.items()
            }
            self._history_store_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            return

    def clear_history(self, chat_id: str) -> None:
        with self._lock:
            self._history.pop(chat_id, None)
            self._save_history_store()

    def record_user_message(self, chat_id: str, user_name: str, message_text: str) -> None:
        formatted_user_message = f"Customer name: {user_name}\nMessage: {message_text}"
        with self._lock:
            self._history[chat_id].append(Message(role="user", content=formatted_user_message))
            self._save_history_store()

    def record_assistant_message(self, chat_id: str, message_text: str) -> None:
        if not message_text.strip():
            return
        with self._lock:
            self._history[chat_id].append(Message(role="assistant", content=message_text.strip()))
            self._save_history_store()

    def route_message(
        self,
        chat_id: str,
        user_name: str,
        message_text: str,
        state_context: str,
        catalog_text: str,
    ) -> RouterDecision:
        with self._lock:
            history = list(self._history[chat_id])

        model_input = [
            {
                "role": message.role,
                "content": message.content,
            }
            for message in history
        ]
        model_input.append(
            {
                "role": "user",
                "content": f"Customer name: {user_name}\nMessage: {message_text}",
            }
        )

        instructions = (
            "You are the conversation router for a WhatsApp sales bot.\n"
            "Return only valid JSON with no markdown, no explanation, and no extra text.\n\n"
            "Allowed actions:\n"
            "- reply\n"
            "- showcase\n"
            "- ask_color\n"
            "- ask_quantity\n"
            "- ask_quantity_text\n"
            "- ask_order_details\n"
            "- show_payment_methods\n"
            "- show_payment_details\n"
            "- send_more_photos\n"
            "- send_color_photos\n"
            "- send_video\n"
            "- schedule_followup\n"
            "- activate_manager\n\n"
            "JSON schema:\n"
            "{\n"
            '  "action": "reply|showcase|ask_color|ask_quantity|ask_quantity_text|ask_order_details|show_payment_methods|show_payment_details|send_more_photos|send_color_photos|send_video|schedule_followup|activate_manager",\n'
            '  "reply_text": "short plain text reply in Russian",\n'
            '  "product_name": "product name if relevant",\n'
            '  "color": "chosen color if relevant",\n'
            '  "quantity": "chosen quantity if relevant",\n'
            '  "customer_full_name": "customer name if provided",\n'
            '  "customer_phone": "customer phone if provided",\n'
            '  "delivery_address": "customer address if provided",\n'
            '  "payment_method": "kaspi_qr|udalenka|",\n'
            '  "reminder_text": "one short Russian follow-up reminder if action=schedule_followup",\n'
            '  "follow_up_delay_minutes": 180\n'
            "}\n\n"
            "Rules:\n"
            "- Always think in terms of the current stage from the provided state.\n"
            "- If the customer is just asking a question, use action=reply.\n"
            "- If the customer wants photos or to see the product, use action=showcase.\n"
            "- If the customer asks to show a specific color, use action=send_color_photos and fill color.\n"
            "- If the customer clearly says they want to think, postpone the decision, or asks to come back later, use action=schedule_followup.\n"
            "- Only use schedule_followup when the customer is clearly postponing the decision. Do not use it for normal product questions.\n"
            "- For schedule_followup, reply_text should confirm politely now, and reminder_text should be a short reminder for later in Russian.\n"
            "- For schedule_followup, use follow_up_delay_minutes=180 unless the customer clearly asks for another delay.\n"
            "- If the customer wants to buy/order and color and quantity are not collected yet, use action=ask_color.\n"
            "- action=ask_color means: ask the customer to send color and quantity together in one text message.\n"
            "- If color is already known but quantity is still unclear, use action=ask_quantity, but still ask in plain text without cards.\n"
            "- If color and quantity are already known and customer details are missing, use action=ask_order_details.\n"
            "- If the chat is awaiting_order_address and the customer sends full name, phone, and address, use action=show_payment_methods.\n"
            "- If customer provides name, phone, and address, use action=show_payment_methods and fill those fields.\n"
            "- If customer chooses Kaspi QR or Удаленка, use action=show_payment_details and set payment_method.\n"
            "- If the customer asks a side question during an order, use action=reply and do not restart the order.\n"
            "- Never send ask_color again if both color and quantity are already chosen.\n"
            "- Never send ask_quantity again if quantity is already chosen.\n"
            "- Never ask for customer details again if they are already saved.\n"
            "- Only use activate_manager if the customer clearly asks for a manager or human.\n"
            "- reply_text must always be short, plain text, and relevant to the user's last message.\n"
            "- When replying, behave like a helpful sales specialist: answer, add one useful benefit, and invite one clear next action.\n\n"
            f"{STYLE_RULES}\n\n"
            f"{state_context}\n\n"
            f"{catalog_text}"
        )

        response = self._client.responses.create(
            model=self._settings.openai_model,
            instructions=instructions,
            input=model_input,
        )
        raw_text = (response.output_text or "").strip()
        payload = self._parse_json_object(raw_text)
        action = str(payload.get("action", "reply")).strip() or "reply"
        return RouterDecision(
            action=action,
            reply_text=self._sanitize_reply(str(payload.get("reply_text", "")).strip()),
            product_name=str(payload.get("product_name", "")).strip(),
            color=str(payload.get("color", "")).strip(),
            quantity=str(payload.get("quantity", "")).strip(),
            customer_full_name=str(payload.get("customer_full_name", "")).strip(),
            customer_phone=str(payload.get("customer_phone", "")).strip(),
            delivery_address=str(payload.get("delivery_address", "")).strip(),
            payment_method=str(payload.get("payment_method", "")).strip(),
            reminder_text=self._sanitize_reply(str(payload.get("reminder_text", "")).strip()),
            follow_up_delay_minutes=self._parse_delay_minutes(payload.get("follow_up_delay_minutes")),
        )

    def generate_reply(
        self,
        chat_id: str,
        user_name: str,
        message_text: str,
        catalog_text: str,
        conversation_started: bool = False,
        allow_order_reminder: bool = False,
    ) -> str:
        with self._lock:
            history = list(self._history[chat_id])

        formatted_user_message = f"Customer name: {user_name}\nMessage: {message_text}"
        extra_rules = ""
        if history or conversation_started:
            extra_rules = (
                "\n\nThe conversation is already in progress. "
                "Do not greet again. Answer directly and continue the current context."
            )
        if not allow_order_reminder:
            extra_rules += (
                "\n\nDo not mention order steps, quantity, address, payment method, "
                "or phrases like 'to continue the order' unless the current order stage "
                "is explicitly provided and the customer is already in that step."
            )
        model_input = [
            {
                "role": message.role,
                "content": message.content,
            }
            for message in history
        ]
        model_input.append(
            {
                "role": "user",
                "content": formatted_user_message,
            }
        )

        response = self._client.responses.create(
            model=self._settings.openai_model,
            instructions=(
                f"{self._settings.bot_system_prompt}\n\n"
                f"{STYLE_RULES}\n\n"
                f"{catalog_text}"
                f"{extra_rules}"
            ),
            input=model_input,
        )
        reply_text = self._sanitize_reply((response.output_text or "").strip())
        if not reply_text:
            raise RuntimeError("OpenAI returned an empty response.")

        with self._lock:
            self._history[chat_id].append(
                Message(role="user", content=formatted_user_message)
            )
            self._history[chat_id].append(Message(role="assistant", content=reply_text))
            self._save_history_store()

        return reply_text

    def _sanitize_reply(self, text: str) -> str:
        text = text.replace("**", "")
        text = text.replace("__", "")
        text = text.replace("`", "")
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)

        cleaned_lines = []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            line = re.sub(r"^[\-\*\u2022]+\s*", "", line)
            cleaned_lines.append(line)

        text = "\n".join(line for line in cleaned_lines if line)
        return text.strip()

    def _parse_json_object(self, text: str) -> dict[str, object]:
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass

        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            snippet = text[start : end + 1]
            try:
                data = json.loads(snippet)
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                pass

        raise RuntimeError("OpenAI router did not return valid JSON.")

    def _parse_delay_minutes(self, value: object) -> int:
        try:
            parsed = int(str(value).strip())
        except (TypeError, ValueError):
            return 0
        return max(0, parsed)
