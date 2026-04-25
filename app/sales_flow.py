from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from collections import deque
from pathlib import Path, PurePosixPath
from threading import Lock
from urllib.parse import urlparse

from app.catalog_service import PaymentDetails, Product
from app.config import Settings


@dataclass
class ChatState:
    selected_product_name: str | None = None
    stage: str = "idle"
    payment_method: str | None = None
    order_color: str = ""
    order_quantity: str = ""
    color_page: int = 0
    next_image_index: int = 0
    video_sent: bool = False
    manager_mode: bool = False
    customer_full_name: str = ""
    customer_phone: str = ""
    delivery_address: str = ""
    remote_kaspi_phone: str = ""
    pending_receipt_info: dict[str, str] = field(default_factory=dict)
    language: str = "ru"
    intro_sent: bool = False
    customer_city: str = ""
    waiting_delivery_city: bool = False


@dataclass
class ScheduledFollowUp:
    due_at: datetime
    reminder_text: str


class SalesFlowService:
    def __init__(self, settings: Settings, state_store_path: Path | None = None) -> None:
        self._settings = settings
        self._states: dict[str, ChatState] = {}
        self._recent_message_ids: deque[str] = deque(maxlen=500)
        self._recent_message_id_set: set[str] = set()
        self._follow_ups: dict[str, ScheduledFollowUp] = {}
        self._follow_up_lock = Lock()
        self._state_store_path = state_store_path
        self._load_state_store()

    def _load_state_store(self) -> None:
        if not self._state_store_path or not self._state_store_path.exists():
            return

        try:
            payload = json.loads(self._state_store_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return

        raw_states = payload.get("states", {})
        if isinstance(raw_states, dict):
            for chat_id, raw_state in raw_states.items():
                if not isinstance(raw_state, dict):
                    continue
                try:
                    self._states[str(chat_id)] = ChatState(**raw_state)
                except TypeError:
                    continue

        raw_follow_ups = payload.get("follow_ups", {})
        if isinstance(raw_follow_ups, dict):
            for chat_id, raw_item in raw_follow_ups.items():
                if not isinstance(raw_item, dict):
                    continue
                due_at_raw = str(raw_item.get("due_at", "")).strip()
                reminder_text = str(raw_item.get("reminder_text", "")).strip()
                if not (due_at_raw and reminder_text):
                    continue
                try:
                    due_at = datetime.fromisoformat(due_at_raw)
                except ValueError:
                    continue
                self._follow_ups[str(chat_id)] = ScheduledFollowUp(
                    due_at=due_at,
                    reminder_text=reminder_text,
                )

    def _save_state_store(self) -> None:
        if not self._state_store_path:
            return

        try:
            self._state_store_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "states": {chat_id: asdict(state) for chat_id, state in self._states.items()},
                "follow_ups": {
                    chat_id: {
                        "due_at": item.due_at.isoformat(),
                        "reminder_text": item.reminder_text,
                    }
                    for chat_id, item in self._follow_ups.items()
                },
            }
            self._state_store_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            return

    def get_state(self, chat_id: str) -> ChatState:
        return self._states.setdefault(chat_id, ChatState())

    def mark_message_processed(self, message_id: str) -> bool:
        normalized = message_id.strip()
        if not normalized:
            return False
        if normalized in self._recent_message_id_set:
            return True

        if len(self._recent_message_ids) == self._recent_message_ids.maxlen:
            oldest = self._recent_message_ids.popleft()
            self._recent_message_id_set.discard(oldest)

        self._recent_message_ids.append(normalized)
        self._recent_message_id_set.add(normalized)
        return False

    def detect_language(self, text: str) -> str:
        return "ru"

    def remember_language(self, chat_id: str, text: str) -> str:
        language = self.detect_language(text)
        self.get_state(chat_id).language = language
        self._save_state_store()
        return language

    def get_language(self, chat_id: str) -> str:
        return "ru"

    def remember_product(self, chat_id: str, product: Product) -> None:
        state = self.get_state(chat_id)
        state.selected_product_name = product.name
        state.stage = "product_presented"
        state.payment_method = None
        state.order_color = ""
        state.order_quantity = ""
        state.next_image_index = 1
        state.video_sent = False
        state.waiting_delivery_city = False
        self._save_state_store()

    def mark_waiting_payment_method(self, chat_id: str, product: Product) -> None:
        state = self.get_state(chat_id)
        state.selected_product_name = product.name
        state.stage = "awaiting_payment_method"
        state.payment_method = None
        state.remote_kaspi_phone = ""
        self._save_state_store()

    def mark_waiting_order_color(self, chat_id: str, product: Product) -> None:
        self.mark_waiting_order_selection(chat_id, product)

    def mark_waiting_order_selection(self, chat_id: str, product: Product) -> None:
        state = self.get_state(chat_id)
        state.selected_product_name = product.name
        state.stage = "awaiting_order_selection"
        state.payment_method = None
        state.order_color = ""
        state.order_quantity = ""
        state.color_page = 0
        state.customer_full_name = ""
        state.customer_phone = ""
        state.delivery_address = ""
        self._save_state_store()

    def mark_waiting_order_quantity(self, chat_id: str) -> None:
        self.get_state(chat_id).stage = "awaiting_order_quantity"
        self._save_state_store()

    def mark_waiting_order_quantity_text(self, chat_id: str) -> None:
        self.get_state(chat_id).stage = "awaiting_order_quantity_text"
        self._save_state_store()

    def mark_waiting_order_address(self, chat_id: str) -> None:
        self.get_state(chat_id).stage = "awaiting_order_address"
        self._save_state_store()

    def mark_payment_method(self, chat_id: str, payment_method: str) -> None:
        state = self.get_state(chat_id)
        state.payment_method = payment_method
        state.stage = "awaiting_receipt"
        self._save_state_store()

    def mark_receipt_logged(self, chat_id: str) -> None:
        state = self.get_state(chat_id)
        state.stage = "receipt_logged"
        state.pending_receipt_info = {}
        self._save_state_store()

    def activate_manager_mode(self, chat_id: str) -> None:
        state = self.get_state(chat_id)
        state.manager_mode = True
        state.stage = "manager_mode"
        self._save_state_store()

    def mark_discovering(self, chat_id: str) -> None:
        state = self.get_state(chat_id)
        state.stage = "discovering"
        state.intro_sent = True
        self._save_state_store()

    def mark_intro_sent(self, chat_id: str, sent: bool = True) -> None:
        self.get_state(chat_id).intro_sent = sent
        self._save_state_store()

    def mark_waiting_delivery_city(self, chat_id: str, waiting: bool = True) -> None:
        self.get_state(chat_id).waiting_delivery_city = waiting
        self._save_state_store()

    def is_waiting_delivery_city(self, chat_id: str) -> bool:
        return self.get_state(chat_id).waiting_delivery_city

    def save_customer_city(self, chat_id: str, city: str) -> None:
        state = self.get_state(chat_id)
        state.customer_city = city.strip()
        state.waiting_delivery_city = False
        self._save_state_store()

    def deactivate_manager_mode(self, chat_id: str) -> None:
        state = self.get_state(chat_id)
        state.manager_mode = False
        state.stage = "idle"
        self._save_state_store()

    def is_manager_mode(self, chat_id: str) -> bool:
        return self.get_state(chat_id).manager_mode

    def schedule_follow_up(self, chat_id: str, reminder_text: str, delay_minutes: int = 180) -> None:
        normalized_delay = max(30, delay_minutes)
        with self._follow_up_lock:
            self._follow_ups[chat_id] = ScheduledFollowUp(
                due_at=datetime.now() + timedelta(minutes=normalized_delay),
                reminder_text=reminder_text.strip(),
            )
        self._save_state_store()

    def cancel_follow_up(self, chat_id: str) -> bool:
        with self._follow_up_lock:
            removed = self._follow_ups.pop(chat_id, None) is not None
        if removed:
            self._save_state_store()
        return removed

    def has_follow_up(self, chat_id: str) -> bool:
        with self._follow_up_lock:
            return chat_id in self._follow_ups

    def get_follow_up_due_text(self, chat_id: str) -> str:
        with self._follow_up_lock:
            follow_up = self._follow_ups.get(chat_id)
        if not follow_up:
            return "none"
        return follow_up.due_at.strftime("%Y-%m-%d %H:%M")

    def pop_due_follow_ups(self) -> list[tuple[str, ScheduledFollowUp]]:
        now = datetime.now()
        due_items: list[tuple[str, ScheduledFollowUp]] = []
        with self._follow_up_lock:
            due_chat_ids = [chat_id for chat_id, item in self._follow_ups.items() if item.due_at <= now]
            for chat_id in due_chat_ids:
                item = self._follow_ups.pop(chat_id, None)
                if item:
                    due_items.append((chat_id, item))
        if due_items:
            self._save_state_store()
        return due_items

    def is_payment_intent(self, text: str) -> bool:
        normalized = text.lower()
        keywords = (
            "оплат",
            "kaspi qr",
            "номер",
            "реквизит",
            "оплачу",
        )
        return any(keyword in normalized for keyword in keywords)

    def is_order_request(self, text: str) -> bool:
        normalized = text.strip().lower()
        exact_phrases = {
            "заказать",
            "оформить",
            "оформить заказ",
            "хочу заказать",
            "хочу оформить заказ",
            "куплю",
            "беру",
            "ок оформить заказ",
        }
        if normalized in exact_phrases:
            return True

        startswith_phrases = (
            "хочу заказать",
            "хочу оформить",
            "давайте оформим заказ",
            "можно оформить заказ",
        )
        return any(normalized.startswith(phrase) for phrase in startswith_phrases)

    def is_more_photos_request(self, text: str) -> bool:
        normalized = text.strip().lower()
        phrases = ("еще фото", "фото", "more photos", "product_more_photos")
        return any(phrase == normalized or phrase in normalized for phrase in phrases)

    def is_video_request(self, text: str) -> bool:
        normalized = text.strip().lower()
        phrases = ("видео", "video", "ролик", "product_video")
        return any(phrase == normalized or phrase in normalized for phrase in phrases)

    def is_buy_now_request(self, text: str) -> bool:
        normalized = text.strip().lower()
        exact_phrases = {
            "заказать",
            "оформить",
            "оформить заказ",
            "buy",
            "buy now",
            "product_payment",
        }
        if normalized in exact_phrases:
            return True

        startswith_phrases = (
            "хочу заказать",
            "хочу оформить",
            "давайте оформим заказ",
            "можно оформить заказ",
        )
        return any(normalized.startswith(phrase) for phrase in startswith_phrases)

    def is_manager_request(self, text: str) -> bool:
        normalized = text.strip().lower()
        phrases = ("менеджер", "оператор", "human", "manager", "product_manager")
        return any(phrase == normalized or phrase in normalized for phrase in phrases)

    def is_greeting(self, text: str) -> bool:
        normalized = text.strip().lower()
        phrases = (
            "привет",
            "здравствуйте",
            "здра",
            "здраст",
            "добрый день",
            "добрый вечер",
            "hello",
            "hi",
        )
        return any(phrase in normalized for phrase in phrases)

    def is_catalog_browse_request(self, text: str) -> bool:
        normalized = text.strip().lower()
        phrases = (
            "каталог",
            "товар",
            "товары",
            "ассортимент",
            "показать",
            "посмотреть",
            "варианты",
            "что есть",
        )
        return any(phrase in normalized for phrase in phrases)

    def is_delivery_question(self, text: str) -> bool:
        normalized = text.strip().lower()
        phrases = (
            "доставка",
            "сколько дней",
            "какой срок",
            "срок доставки",
            "когда доставка",
            "по времени",
            "в какой город",
            "в город",
            "район",
            "город",
        )
        return any(phrase in normalized for phrase in phrases)

    def is_why_question(self, text: str) -> bool:
        normalized = text.strip().lower()
        phrases = (
            "зачем",
            "для чего",
            "почему",
            "почему нужно",
            "не үшін",
            "не ушін",
            "не ушин",
            "неге",
        )
        return any(phrase in normalized for phrase in phrases)

    def is_color_question(self, text: str) -> bool:
        normalized = text.strip().lower()
        phrases = ("цвет", "цвета", "какие цвета", "в каком цвете")
        return any(phrase in normalized for phrase in phrases)

    def extract_city(self, text: str) -> str:
        normalized = text.strip().lower()
        if not normalized:
            return ""

        known_cities = {
            "алматы": "Алматы",
            "астана": "Астана",
            "шымкент": "Шымкент",
            "караганда": "Караганда",
            "қарағанды": "Караганда",
            "семей": "Семей",
            "павлодар": "Павлодар",
            "актау": "Актау",
            "актобе": "Актобе",
            "ақтөбе": "Актобе",
            "атырау": "Атырау",
            "костанай": "Костанай",
            "қостанай": "Костанай",
            "кокшетау": "Кокшетау",
            "өкшетау": "Кокшетау",
            "петропавловск": "Петропавловск",
            "тараз": "Тараз",
            "уральск": "Уральск",
            "орал": "Уральск",
            "усть-каменогорск": "Усть-Каменогорск",
            "усть каменогорск": "Усть-Каменогорск",
            "оскемен": "Усть-Каменогорск",
            "өскемен": "Усть-Каменогорск",
            "кызылорда": "Кызылорда",
            "қызылорда": "Кызылорда",
            "туркестан": "Туркестан",
        }
        for key, value in known_cities.items():
            if re.search(rf"(?<!\w){re.escape(key)}(?!\w)", normalized):
                return value

        if re.fullmatch(r"[a-zA-Zа-яА-ЯёЁқҚәӘөӨұҰүҮһҺіІ\- ]{2,30}", text.strip()):
            return text.strip().title()

        return ""

    def build_initial_prompt(self, language: str, include_greeting: bool = True) -> str:
        if include_greeting:
            return (
                "Здравствуйте!\n"
                "Подскажите, что вам важнее: цена, фото, цвет или доставка? Я помогу быстро подобрать вариант."
            )
        return "Подскажите, что показать сначала: цену, фото, цвета или доставку?"

    def build_offer_message(self, product: Product, language: str) -> str:
        lines = [f"Показываю модель {product.name}."]

        if product.current_price:
            lines.append(f"Сейчас цена {product.current_price}.")
        if product.regular_price and product.regular_price != product.current_price:
            lines.append(f"Раньше была {product.regular_price}, сейчас выгоднее оформить по акции.")
        if product.kaspi_installment:
            lines.append(f"Можно через Kaspi: {product.kaspi_installment}.")
        if product.colors:
            lines.append(f"По цветам есть: {product.colors}.")
        if product.description:
            lines.append(product.description)

        lines.append("Какой цвет хотите посмотреть ближе?")
        return "\n".join(lines)

    def build_order_color_prompt(self, product: Product | None) -> str:
        return self.build_order_selection_prompt(product)

    def build_order_selection_prompt(self, product: Product | None) -> str:
        if product and product.colors:
            return (
                "Отлично, оформим.\n"
                f"Напишите нужный цвет и количество одним сообщением. Доступные цвета: {product.colors}.\n"
                "Пример: Черный, 2 шт."
            )
        return "Отлично, оформим. Напишите цвет и количество одним сообщением. Пример: Черный, 2 шт."

    def build_order_quantity_prompt(self) -> str:
        return "Сколько штук вам нужно?"

    def build_order_quantity_text_prompt(self) -> str:
        return "Напишите, пожалуйста, точное количество цифрой."

    def build_order_address_prompt(self) -> str:
        return (
            "Приняла, осталось оформить доставку.\n"
            "Отправьте данные одним сообщением:\n"
            "Имя: ...\n"
            "Телефон: ...\n"
            "Адрес: ..."
        )

    def build_order_summary(self, state: ChatState, product: Product | None) -> str:
        lines = ["Ваш заказ:"]
        if product:
            lines.append(f"Товар: {product.name}")
        if state.order_color:
            lines.append(f"Цвет: {state.order_color}")
        if state.order_quantity:
            lines.append(f"Количество: {state.order_quantity} шт.")
        if state.customer_full_name:
            lines.append(f"Имя: {state.customer_full_name}")
        if state.customer_phone:
            lines.append(f"Телефон: {state.customer_phone}")
        if state.delivery_address:
            lines.append(f"Адрес: {state.delivery_address}")
        total_price = self.calculate_total_price(product, state.order_quantity)
        if total_price:
            lines.append(f"Сумма к оплате: {total_price}.")
        return "\n".join(lines)

    def is_kaspi_selection(self, text: str) -> bool:
        normalized = text.strip().lower()
        return normalized in {"kaspi qr", "kaspi", "payment_kaspi_qr", "payment_kaspi"}

    def build_kaspi_details_message(
        self,
        product: Product | None,
        state: ChatState,
        language: str,
        payment_details: PaymentDetails | None = None,
    ) -> str:
        payment_details = payment_details or PaymentDetails()
        lines = [self.build_order_summary(state, product), "", "Ниже отправляю Kaspi QR для оплаты."]
        total_price = self.calculate_total_price(product, state.order_quantity)
        if total_price:
            lines.append(f"Сумма: {total_price}.")
        lines.append("После оплаты отправьте, пожалуйста, чек для подтверждения оплаты.")
        return "\n".join(lines)

    def calculate_total_price(self, product: Product | None, quantity_text: str) -> str:
        if not product:
            return ""
        quantity = self._safe_int(quantity_text) or 1
        return self._multiply_amount_text(product.current_price, quantity)

    def calculate_total_price_numeric(self, product: Product | None, quantity_text: str) -> str:
        if not product:
            return ""
        quantity = self._safe_int(quantity_text) or 1
        amount = self._extract_amount_number(product.current_price)
        if amount is None:
            return ""
        return str(amount * max(quantity, 1))

    def _safe_int(self, value: str) -> int | None:
        match = re.search(r"\d+", value or "")
        if not match:
            return None
        return int(match.group(0))

    def _multiply_amount_text(self, amount_text: str, quantity: int) -> str:
        raw = (amount_text or "").strip()
        if not raw:
            return ""

        amount = self._extract_amount_number(raw)
        if amount is None:
            return raw

        total = amount * max(quantity, 1)
        prefix = "~" if "~" in raw else ""

        lower = raw.lower()
        suffix = ""
        if "тг" in lower:
            suffix = " тг"
        elif "₸" in raw:
            suffix = " ₸"

        return f"{prefix}{total:,}".replace(",", " ") + suffix

    def _extract_amount_number(self, amount_text: str) -> int | None:
        digits = re.findall(r"\d+", amount_text or "")
        if not digits:
            return None
        return int("".join(digits))

    def build_manager_handoff_message(self, language: str) -> str:
        return "Хорошо, подключаю менеджера.\nДальше вам ответит человек."

    def build_follow_up_ack_message(self) -> str:
        return "Хорошо, понимаю.\nНапомню чуть позже, чтобы вы спокойно могли вернуться к выбору."

    def build_follow_up_reminder(self, product: Product | None = None) -> str:
        if product:
            return (
                f"Здравствуйте! Напоминаю по {product.name}.\n"
                "Если еще актуально, могу сразу подсказать по цвету, доставке или оформить заказ."
            )
        return (
            "Здравствуйте! Напоминаю по вашему запросу.\n"
            "Если еще актуально, могу подсказать по цене, доставке или оформлению."
        )

    def build_waiting_customer_details_message(self, language: str) -> str:
        return (
            "Чек получили.\n"
            "Теперь отправьте данные одним сообщением.\n"
            "Формат:\nИмя: ...\nТелефон: ...\nАдрес: ..."
        )

    def build_waiting_receipt_message(self, language: str) -> str:
        return "Заказные данные получили. Теперь отправьте чек или скрин оплаты."

    def build_waiting_order_details_message(self) -> str:
        return (
            "Пожалуйста, отправьте данные одним сообщением в таком формате:\n"
            "Имя: ...\n"
            "Телефон: ...\n"
            "Адрес: ..."
        )

    def build_order_details_explanation(self) -> str:
        return (
            "Эти данные нужны, чтобы правильно оформить заказ и доставку.\n"
            "Отправьте одним сообщением:\n"
            "Имя: ...\n"
            "Телефон: ...\n"
            "Адрес: ..."
        )

    def build_delivery_prompt(self) -> str:
        return "Подскажите, пожалуйста, в какой город нужна доставка?"

    def build_delivery_message(self, product: Product | None, city: str = "") -> str:
        parts: list[str] = []
        if city:
            parts.append(f"В {city} доставка обычно занимает 2-5 дней.")
        elif product and product.delivery_time:
            parts.append(f"Срок доставки обычно {product.delivery_time}.")
        else:
            parts.append("Срок доставки обычно 2-5 дней.")

        if product and product.delivery_fee:
            parts.append(f"Стоимость доставки: {product.delivery_fee}.")
        else:
            parts.append("Стоимость доставки по Казахстану: 10 000 тг.")

        parts.append("Хотите, я сразу подберу цвет и посчитаю итоговую сумму?")
        return "\n".join(parts)

    def build_colors_message(self, product: Product | None) -> str:
        if product and product.colors:
            return f"Доступные цвета: {product.colors}.\nКакой цвет показать фото?"
        return "По цветам подскажу чуть позже, сейчас уточняю актуальное наличие."

    def build_color_selected_message(self, color: str, in_order_flow: bool = False) -> str:
        if in_order_flow:
            return f"Цвет {color} есть. Сколько штук оформить?"
        return f"Да, цвет {color} есть в наличии.\nХотите покажу фото именно в этом цвете?"

    def match_known_color(self, product: Product | None, text: str) -> str:
        if not product or not product.colors:
            return ""
        normalized = text.strip().lower()
        for option in [item.strip() for item in product.colors.split(",") if item.strip()]:
            option_lower = option.lower()
            if option_lower in normalized:
                return option
        return ""

    def save_customer_details(self, chat_id: str, full_name: str, phone: str, address: str) -> None:
        state = self.get_state(chat_id)
        if full_name:
            state.customer_full_name = full_name
        if phone:
            state.customer_phone = phone
        if address:
            state.delivery_address = address
        self._save_state_store()

    def save_order_details(self, chat_id: str, color: str, address: str) -> None:
        state = self.get_state(chat_id)
        if color:
            state.order_color = color
        if address:
            state.delivery_address = address
        self._save_state_store()

    def _normalize_color_token(self, value: str) -> str:
        token = value.strip().lower()
        replacements = {
            " ": "_",
            "-": "_",
            "/": "_",
            ".": "",
            ",": "",
        }
        for old, new in replacements.items():
            token = token.replace(old, new)
        return token

    def save_order_quantity(self, chat_id: str, quantity: str) -> None:
        self.get_state(chat_id).order_quantity = quantity.strip()
        self._save_state_store()

    def save_pending_receipt(self, chat_id: str, receipt_info: dict[str, str]) -> None:
        state = self.get_state(chat_id)
        state.pending_receipt_info = dict(receipt_info)
        self._save_state_store()

    def get_pending_receipt(self, chat_id: str) -> dict[str, str]:
        return dict(self.get_state(chat_id).pending_receipt_info)

    def has_required_customer_details(self, chat_id: str) -> bool:
        state = self.get_state(chat_id)
        return bool(state.customer_full_name and state.customer_phone and state.delivery_address)

    def has_pending_receipt(self, chat_id: str) -> bool:
        return bool(self.get_state(chat_id).pending_receipt_info)

    def parse_customer_details(self, text: str) -> tuple[str, str, str]:
        normalized = text.strip()
        if not normalized:
            return "", "", ""

        full_name = ""
        phone = ""
        address = ""

        name_match = re.search(
            r"(фио|name|имя)\s*:\s*(.+)",
            normalized,
            flags=re.IGNORECASE,
        )
        if name_match:
            full_name = name_match.group(2).splitlines()[0].strip()

        phone_match = re.search(
            r"(телефон|тел|номер|phone)\s*:\s*(.+)",
            normalized,
            flags=re.IGNORECASE,
        )
        if phone_match:
            phone = phone_match.group(2).splitlines()[0].strip()
        elif self.is_phone_like(normalized):
            phone = normalized

        address_match = re.search(
            r"(адрес|address)\s*:\s*(.+)",
            normalized,
            flags=re.IGNORECASE,
        )
        if address_match:
            address = address_match.group(2).splitlines()[0].strip()

        return full_name, phone, address

    def parse_order_details(self, text: str, product: Product | None) -> tuple[str, str]:
        normalized = text.strip()
        if not normalized:
            return "", ""

        color = ""
        address = ""

        color_match = re.search(r"(цвет|color)\s*:\s*(.+)", normalized, flags=re.IGNORECASE)
        if color_match:
            color = color_match.group(2).splitlines()[0].strip()
        elif product and product.colors:
            for option in [item.strip() for item in product.colors.split(",") if item.strip()]:
                if option.lower() in normalized.lower():
                    color = option
                    break
                if normalized.lower() == f"color_{self._normalize_color_token(option)}":
                    color = option
                    break

        address_match = re.search(r"(адрес|address)\s*:\s*(.+)", normalized, flags=re.IGNORECASE)
        if address_match:
            address = address_match.group(2).splitlines()[0].strip()

        return color, address

    def parse_order_selection(self, text: str, product: Product | None) -> tuple[str, str]:
        color = self.match_known_color(product, text)
        quantity = self.parse_quantity(text)
        return color, quantity

    def is_phone_like(self, text: str) -> bool:
        digits = re.sub(r"\D", "", text)
        return 10 <= len(digits) <= 12 and len(digits) == len(re.sub(r"\s+", "", re.sub(r"[+\-()]", "", text)))

    def parse_quantity(self, text: str) -> str:
        match = re.search(r"\d+", text)
        return match.group(0) if match else ""

    def build_receipt_confirmation(self, product: Product | None, language: str) -> str:
        if product:
            return (
                f"Чек получили. Оплату по товару {product.name} отметили.\n"
                "Дальше с вами свяжется менеджер."
            )
        return "Чек получили. Оплату отметили.\nДальше с вами свяжется менеджер."

    def get_primary_image(self, product: Product) -> str | None:
        return product.image_refs[0] if product.image_refs else None

    def get_primary_video(self, product: Product) -> str | None:
        return product.video_refs[0] if product.video_refs else None

    def get_next_image(self, chat_id: str, product: Product) -> tuple[str | None, bool]:
        state = self.get_state(chat_id)
        if state.next_image_index >= len(product.image_refs):
            return None, True

        ref = product.image_refs[state.next_image_index]
        state.next_image_index += 1
        exhausted = state.next_image_index >= len(product.image_refs)
        self._save_state_store()
        return ref, exhausted

    def get_video(self, chat_id: str, product: Product) -> tuple[str | None, bool]:
        state = self.get_state(chat_id)
        if not product.video_refs:
            return None, True
        already_sent = state.video_sent
        state.video_sent = True
        self._save_state_store()
        return product.video_refs[0], already_sent

    def build_remote_file_name(self, url: str) -> str:
        parsed = urlparse(url)
        name = PurePosixPath(parsed.path).name
        return name or "media-file"
