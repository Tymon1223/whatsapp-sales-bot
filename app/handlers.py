from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.ai_service import RouterDecision
from app.message_utils import (
    extract_file_info,
    extract_message_text,
    get_message_id,
    get_message_type,
    get_sender_name,
    is_group_chat,
)
from app.runtime import BotRuntime


logger = logging.getLogger("whatsapp-ai-bot")

ORDER_STAGES = {
    "awaiting_order_selection",
    "awaiting_order_address",
}

UNIVERSAL_SALES_TEXT = (
    "Здравствуйте! Да, LUNA сейчас в наличии.\n\n"
    "Это туалетный столик с зеркалом для аккуратной beauty-зоны: мягкая обивка, округлые формы, сиденье и место для хранения.\n\n"
    "Сейчас цена 100 000 тг вместо 150 000 тг.\n"
    "Цвета: белый, серый, черный, красный, коричневый, розовый, бирюзовый.\n\n"
    "Какой цвет вам ближе? Могу сразу показать фото в нужном оттенке."
)


def _handle_universal_template(runtime: BotRuntime, notification: Any, incoming_text: str) -> bool:
    state = runtime.sales_flow.get_state(notification.chat)
    if state.stage != "idle" or state.intro_sent:
        return False

    if runtime.catalog_service.find_best_product(incoming_text):
        return False

    if runtime.sales_flow.is_payment_intent(incoming_text):
        return False

    product = runtime.catalog_service.get_default_product()
    media_sent = False
    if product:
        primary_image = runtime.sales_flow.get_primary_image(product)
        if primary_image:
            media_sent = _try_send_media(runtime, notification, primary_image, UNIVERSAL_SALES_TEXT)
        primary_video = runtime.sales_flow.get_primary_video(product)
        if primary_video:
            _try_send_media(runtime, notification, primary_video, None)
        runtime.sales_flow.remember_product(notification.chat, product)
        runtime.sales_flow.mark_intro_sent(notification.chat, True)

    if not media_sent:
        notification.answer(UNIVERSAL_SALES_TEXT)
        runtime.sales_flow.mark_discovering(notification.chat)
    if product:
        _send_product_actions(runtime, notification)

    runtime.ai_service.record_assistant_message(notification.chat, UNIVERSAL_SALES_TEXT)
    logger.info("Universal sales template sent to %s", notification.chat)
    return True


def handle_start(runtime: BotRuntime, notification: Any) -> None:
    sender_name = get_sender_name(notification.event)
    notification.answer(
        (
            f"Здравствуйте, {sender_name}! "
            "Я AI-помощник по продажам в WhatsApp. "
            "Напишите название товара или вопрос по цене. "
            "Для очистки истории отправьте /clear."
        )
    )


def handle_clear(runtime: BotRuntime, notification: Any) -> None:
    runtime.ai_service.clear_history(notification.chat)
    notification.answer("История диалога очищена. Можете отправить новый вопрос.")


def _resolve_local_media_path(runtime: BotRuntime, ref: str) -> Path | None:
    candidate = Path(ref)
    candidates = [candidate]

    if not candidate.is_absolute():
        candidates.extend(
            [
                runtime.project_root / candidate,
                runtime.project_root / "assets" / candidate,
                runtime.project_root / "assets" / "products" / candidate,
                runtime.project_root / "assets" / "payment" / candidate,
            ]
        )

    for path in candidates:
        if path.exists() and path.is_file():
            return path

    return None


def _send_media(
    runtime: BotRuntime,
    notification: Any,
    ref: str,
    caption: str | None = None,
) -> None:
    if ref.startswith(("http://", "https://")):
        file_name = runtime.sales_flow.build_remote_file_name(ref)
        notification.api.sending.sendFileByUrl(
            notification.chat,
            ref,
            file_name,
            caption=caption,
        )
        return

    local_path = _resolve_local_media_path(runtime, ref)
    if not local_path:
        raise FileNotFoundError(f"Media file not found: {ref}")

    notification.answer_with_file(
        str(local_path),
        file_name=local_path.name,
        caption=caption,
    )


def _try_send_media(
    runtime: BotRuntime,
    notification: Any,
    ref: str,
    caption: str | None = None,
) -> bool:
    try:
        _send_media(runtime, notification, ref, caption)
        return True
    except FileNotFoundError:
        logger.warning("Media file not found for %s: %s", notification.chat, ref)
        return False


def _resolve_kaspi_qr_path(runtime: BotRuntime) -> str | None:
    candidates: list[str] = []
    if runtime.settings.payment_kaspi_qr_file:
        candidates.append(runtime.settings.payment_kaspi_qr_file)

    candidates.extend(
        [
            "assets/payment/kaspiqr.jpeg",
            "assets/payment/kaspiqr.jpg",
            "assets/payment/kaspi_qr.jpeg",
            "assets/payment/kaspi_qr.jpg",
            "assets/payment/kaspiqr.png",
            "assets/payment/kaspi_qr.png",
        ]
    )

    for ref in candidates:
        local_path = _resolve_local_media_path(runtime, ref)
        if local_path:
            return str(local_path)

    return None


def _send_product_actions(runtime: BotRuntime, notification: Any) -> None:
    notification.answer_with_interactive_buttons_reply(
        body="Если нужно, выберите действие ниже.",
        buttons=runtime.sales_flow.build_product_action_buttons("ru"),
    )


def _send_list_message(
    notification: Any,
    message: str,
    button_text: str,
    rows: list[dict[str, str]],
    title: str | None = None,
    footer: str | None = None,
) -> None:
    notification.api.sending.sendListMessage(
        notification.chat,
        message,
        button_text,
        sections=[{"title": title or "Варианты", "rows": rows}],
        title=title,
        footer=footer,
    )


def _send_order_color_list(runtime: BotRuntime, notification: Any, product: Any, body: str | None = None) -> None:
    buttons = runtime.sales_flow.build_color_buttons(product, runtime.sales_flow.get_color_page(notification.chat))
    if not buttons:
        notification.answer(body or runtime.sales_flow.build_order_color_prompt(product))
        return

    notification.answer_with_interactive_buttons_reply(
        body=body or runtime.sales_flow.build_order_color_prompt(product),
        buttons=buttons,
    )


def _send_order_quantity_list(runtime: BotRuntime, notification: Any, body: str | None = None) -> None:
    rows = [
        {"title": "1", "rowId": "1", "description": "1 штука"},
        {"title": "2", "rowId": "2", "description": "2 штуки"},
        {"title": "3", "rowId": "3", "description": "3 штуки"},
        {"title": "Больше 5", "rowId": "Больше 5", "description": "Указать точное количество"},
    ]
    _send_list_message(
        notification,
        body or runtime.sales_flow.build_order_quantity_prompt(),
        "Выбрать количество",
        rows,
        title="Количество",
    )


def _finalize_receipt_if_ready(
    runtime: BotRuntime,
    chat_id: str,
    whatsapp_name: str,
) -> bool:
    if not runtime.sales_flow.has_pending_receipt(chat_id):
        return False
    if not runtime.sales_flow.has_required_customer_details(chat_id):
        return False

    state = runtime.sales_flow.get_state(chat_id)
    product = runtime.catalog_service.find_product_by_name(state.selected_product_name)
    receipt_info = runtime.sales_flow.get_pending_receipt(chat_id)
    runtime.payment_logger.log_receipt(
        chat_id=chat_id,
        whatsapp_name=whatsapp_name,
        customer_full_name=state.customer_full_name,
        customer_phone=state.customer_phone,
        delivery_address=state.delivery_address,
        order_color=state.order_color,
        order_quantity=state.order_quantity,
        product=product,
        payment_method=state.payment_method or "",
        receipt_info=receipt_info,
    )
    runtime.sales_flow.mark_receipt_logged(chat_id)
    return True


def _finalize_remote_status(
    runtime: BotRuntime,
    chat_id: str,
    whatsapp_name: str,
    status: str,
) -> None:
    state = runtime.sales_flow.get_state(chat_id)
    product = runtime.catalog_service.find_product_by_name(state.selected_product_name)
    runtime.payment_logger.log_client_status(
        chat_id=chat_id,
        whatsapp_name=whatsapp_name,
        customer_full_name=state.customer_full_name,
        customer_phone=state.customer_phone,
        delivery_address=state.delivery_address,
        order_color=state.order_color,
        order_quantity=state.order_quantity,
        product=product,
        payment_method=state.payment_method or "udalenka",
        status=status,
        receipt_info={},
        remote_kaspi_phone=state.remote_kaspi_phone,
    )
    runtime.sales_flow.mark_receipt_logged(chat_id)


def _build_selected_product_catalog_text(runtime: BotRuntime, chat_id: str, incoming_text: str) -> str:
    state = runtime.sales_flow.get_state(chat_id)
    product = runtime.catalog_service.find_product_by_name(state.selected_product_name)
    if not product:
        return runtime.catalog_service.get_catalog_text(incoming_text)

    return "\n".join(
        [
            "Catalog status: available.",
            "Use only the product and facts listed below when answering.",
            "The customer is already discussing this selected product.",
            "Relevant catalog entry:",
            product.to_context_line(),
        ]
    )


def _build_order_stage_context(runtime: BotRuntime, chat_id: str, incoming_text: str) -> str:
    base = _build_selected_product_catalog_text(runtime, chat_id, incoming_text)
    state = runtime.sales_flow.get_state(chat_id)
    details = [
        f"Current order stage: {state.stage}",
        f"Selected color: {state.order_color or 'not chosen yet'}",
        f"Selected quantity: {state.order_quantity or 'not chosen yet'}",
        f"Customer name: {state.customer_full_name or 'not provided yet'}",
        f"Customer phone: {state.customer_phone or 'not provided yet'}",
        f"Delivery address: {state.delivery_address or 'not provided yet'}",
    ]
    return base + "\n" + "\n".join(details)


def _build_pending_step_reminder(runtime: BotRuntime, chat_id: str) -> str:
    state = runtime.sales_flow.get_state(chat_id)
    product = runtime.catalog_service.find_product_by_name(state.selected_product_name)

    if state.stage == "awaiting_order_selection":
        colors = product.colors if product and product.colors else ""
        if colors:
            return f"Чтобы продолжить заказ, напишите цвет и количество одним сообщением. Доступные цвета: {colors}."
        return "Чтобы продолжить заказ, напишите цвет и количество одним сообщением. Пример: Черный, 2 шт."

    if state.stage == "awaiting_order_address":
        return "Чтобы продолжить заказ, отправьте одним сообщением имя, телефон и полный адрес."

    return ""


def _append_pending_step_reminder(runtime: BotRuntime, chat_id: str, message: str) -> str:
    state = runtime.sales_flow.get_state(chat_id)
    if state.stage not in ORDER_STAGES:
        return message

    reminder = _build_pending_step_reminder(runtime, chat_id)
    if not reminder:
        return message
    if reminder in message:
        return message
    return f"{message}\n{reminder}"


def _handle_order_stage_ai_follow_up(
    runtime: BotRuntime,
    notification: Any,
    incoming_text: str,
    sender_name: str,
) -> bool:
    state = runtime.sales_flow.get_state(notification.chat)
    if state.stage not in ORDER_STAGES:
        return False

    catalog_text = _build_order_stage_context(runtime, notification.chat, incoming_text)
    reply_text = runtime.ai_service.generate_reply(
        chat_id=notification.chat,
        user_name=sender_name,
        message_text=incoming_text,
        catalog_text=(
            f"{catalog_text}\n"
            "The customer is in the middle of an order flow. "
            "Answer the side question briefly and naturally. "
            "Do not restart the scenario, do not ask to choose color again unless the customer asked about colors. "
            "After answering, remind the customer what step is still required to continue the order."
        ),
        conversation_started=runtime.sales_flow.get_state(notification.chat).intro_sent,
        allow_order_reminder=True,
    )
    reminder = _build_pending_step_reminder(runtime, notification.chat)
    if reminder and reminder not in reply_text:
        reply_text = f"{reply_text}\n{reminder}"

    notification.answer(reply_text)
    logger.info("Order-stage AI follow-up sent to %s", notification.chat)
    return True


def _handle_product_offer(runtime: BotRuntime, notification: Any, incoming_text: str) -> bool:
    language = runtime.sales_flow.remember_language(notification.chat, incoming_text)
    product = runtime.catalog_service.find_best_product(incoming_text)
    if not product:
        return False

    primary_image = runtime.sales_flow.get_primary_image(product)
    if primary_image:
        _try_send_media(runtime, notification, primary_image, product.name)
    primary_video = runtime.sales_flow.get_primary_video(product)
    if primary_video:
        _try_send_media(runtime, notification, primary_video, None)
    notification.answer(runtime.sales_flow.build_offer_message(product, language))
    _send_product_actions(runtime, notification)

    runtime.sales_flow.remember_product(notification.chat, product)
    logger.info("Product offer sent to %s for %s", notification.chat, product.name)
    return True


def _handle_discovery_showcase(runtime: BotRuntime, notification: Any, incoming_text: str) -> bool:
    state = runtime.sales_flow.get_state(notification.chat)
    if state.stage != "discovering":
        return False

    language = runtime.sales_flow.remember_language(notification.chat, incoming_text)
    product = runtime.catalog_service.find_best_product(incoming_text) or runtime.catalog_service.get_default_product()
    if not product:
        return False

    primary_image = runtime.sales_flow.get_primary_image(product)
    if primary_image:
        _try_send_media(runtime, notification, primary_image, product.name)
    primary_video = runtime.sales_flow.get_primary_video(product)
    if primary_video:
        _try_send_media(runtime, notification, primary_video, None)
    notification.answer(runtime.sales_flow.build_offer_message(product, language))
    _send_product_actions(runtime, notification)

    runtime.sales_flow.remember_product(notification.chat, product)
    logger.info("Discovery showcase sent to %s for %s", notification.chat, product.name)
    return True


def _handle_order_request(runtime: BotRuntime, notification: Any, incoming_text: str) -> bool:
    if not (runtime.sales_flow.is_order_request(incoming_text) or runtime.sales_flow.is_buy_now_request(incoming_text)):
        return False

    state = runtime.sales_flow.get_state(notification.chat)
    if state.stage in ORDER_STAGES:
        notification.answer(_build_pending_step_reminder(runtime, notification.chat))
        logger.info("Reminded pending order step to %s", notification.chat)
        return True

    if state.stage not in {"product_presented", "discovering"}:
        return False

    product = runtime.catalog_service.find_product_by_name(state.selected_product_name) or runtime.catalog_service.find_best_product(
        incoming_text
    )
    if not product:
        return False

    _send_order_color_list(runtime, notification, product)
    runtime.sales_flow.mark_waiting_order_color(notification.chat, product)
    logger.info("Order color requested from %s for %s", notification.chat, product.name)
    return True


def _handle_order_details_submission(runtime: BotRuntime, notification: Any, incoming_text: str) -> bool:
    state = runtime.sales_flow.get_state(notification.chat)
    product = runtime.catalog_service.find_product_by_name(state.selected_product_name)

    if state.stage == "awaiting_order_selection":
        color, quantity = runtime.sales_flow.parse_order_selection(incoming_text, product)
        if not (color and quantity):
            notification.answer(runtime.sales_flow.build_order_selection_prompt(product))
            logger.info("Waiting for combined color and quantity from %s", notification.chat)
            return True

        runtime.sales_flow.save_order_details(notification.chat, color, "")
        runtime.sales_flow.save_order_quantity(notification.chat, quantity)
        runtime.sales_flow.mark_waiting_order_address(notification.chat)
        notification.answer(runtime.sales_flow.build_order_address_prompt())
        logger.info("Order selection captured for %s: %s / %s", notification.chat, color, quantity)
        return True

    if state.stage == "awaiting_order_address":
        if runtime.sales_flow.is_why_question(incoming_text):
            notification.answer(runtime.sales_flow.build_order_details_explanation())
            logger.info("Explained order details request to %s", notification.chat)
            return True

        full_name, phone, address = runtime.sales_flow.parse_customer_details(incoming_text)
        if not (full_name and phone and address):
            notification.answer(runtime.sales_flow.build_waiting_order_details_message())
            logger.info("Waiting for full order details from %s", notification.chat)
            return True
        if not product:
            return False
        runtime.sales_flow.save_customer_details(notification.chat, full_name, phone, address)
        runtime.sales_flow.save_order_details(notification.chat, "", address)
        runtime.sales_flow.mark_waiting_payment_method(notification.chat, product)
        notification.answer_with_interactive_buttons_reply(
            body=(
                f"{runtime.sales_flow.build_order_summary(runtime.sales_flow.get_state(notification.chat), product)}\n"
                "Выберите способ оплаты:"
            ),
            buttons=runtime.sales_flow.build_payment_buttons("ru"),
        )
        logger.info("Full order details captured for %s", notification.chat)
        return True

    return False


def _handle_product_follow_up(runtime: BotRuntime, notification: Any, incoming_text: str) -> bool:
    state = runtime.sales_flow.get_state(notification.chat)
    if state.stage not in {
        "product_presented",
        "awaiting_payment_method",
        "awaiting_order_color",
        "awaiting_order_quantity",
        "awaiting_order_quantity_text",
        "awaiting_order_address",
    }:
        return False

    product = runtime.catalog_service.find_product_by_name(state.selected_product_name)
    if not product:
        return False

    city = runtime.sales_flow.extract_city(incoming_text)
    matched_color = runtime.sales_flow.match_known_color(product, incoming_text)

    if runtime.sales_flow.is_waiting_delivery_city(notification.chat) and city:
        runtime.sales_flow.save_customer_city(notification.chat, city)
        notification.answer(
            _append_pending_step_reminder(
                runtime,
                notification.chat,
                runtime.sales_flow.build_delivery_message(product, city),
            )
        )
        logger.info("Delivery city captured for %s: %s", notification.chat, city)
        return True

    if city:
        runtime.sales_flow.save_customer_city(notification.chat, city)
        notification.answer(
            _append_pending_step_reminder(
                runtime,
                notification.chat,
                runtime.sales_flow.build_delivery_message(product, city),
            )
        )
        logger.info("Delivery city inferred for %s: %s", notification.chat, city)
        return True

    if runtime.sales_flow.is_delivery_question(incoming_text):
        if city:
            runtime.sales_flow.save_customer_city(notification.chat, city)
            notification.answer(
                _append_pending_step_reminder(
                    runtime,
                    notification.chat,
                    runtime.sales_flow.build_delivery_message(product, city),
                )
            )
        else:
            runtime.sales_flow.mark_waiting_delivery_city(notification.chat, True)
            notification.answer(
                _append_pending_step_reminder(
                    runtime,
                    notification.chat,
                    runtime.sales_flow.build_delivery_prompt(),
                )
            )
        logger.info("Delivery follow-up handled for %s", notification.chat)
        return True

    if matched_color:
        in_order_flow = state.stage in ORDER_STAGES
        notification.answer(
            _append_pending_step_reminder(
                runtime,
                notification.chat,
                runtime.sales_flow.build_color_selected_message(matched_color, in_order_flow=in_order_flow),
            )
        )
        logger.info("Direct color match handled for %s: %s", notification.chat, matched_color)
        return True

    if runtime.sales_flow.is_color_question(incoming_text):
        notification.answer(
            _append_pending_step_reminder(
                runtime,
                notification.chat,
                runtime.sales_flow.build_colors_message(product),
            )
        )
        logger.info("Color follow-up handled for %s", notification.chat)
        return True

    return False


def _handle_product_action(runtime: BotRuntime, notification: Any, incoming_text: str) -> bool:
    state = runtime.sales_flow.get_state(notification.chat)
    if state.stage not in {"product_presented", "awaiting_payment_method"}:
        return False

    product = runtime.catalog_service.find_product_by_name(state.selected_product_name)
    if not product:
        return False

    if runtime.sales_flow.is_manager_request(incoming_text):
        runtime.sales_flow.activate_manager_mode(notification.chat)
        notification.answer(
            runtime.sales_flow.build_manager_handoff_message(
                runtime.sales_flow.get_language(notification.chat)
            )
        )
        logger.info("Manager mode activated by customer request for %s", notification.chat)
        return True

    if runtime.sales_flow.is_more_photos_request(incoming_text):
        next_image, exhausted = runtime.sales_flow.get_next_image(notification.chat, product)
        if not next_image:
            notification.answer("Дополнительные фото закончились. Можете выбрать Видео, Оплата или Менеджер.")
            _send_product_actions(runtime, notification)
            return True

        if not _try_send_media(runtime, notification, next_image, None):
            notification.answer("Дополнительное фото пока недоступно. Можете выбрать Оплата или Менеджер.")
            _send_product_actions(runtime, notification)
            return True
        if exhausted:
            notification.answer("Это было последнее фото. Можете выбрать Видео, Оплата или Менеджер.")
        else:
            _send_product_actions(runtime, notification)
        return True

    if runtime.sales_flow.is_video_request(incoming_text):
        video_ref, already_sent = runtime.sales_flow.get_video(notification.chat, product)
        if not video_ref:
            notification.answer("По этому товару видео пока нет. Можете выбрать Еще фото, Оплата или Менеджер.")
            _send_product_actions(runtime, notification)
            return True

        if not _try_send_media(runtime, notification, video_ref, None):
            notification.answer("Видео пока недоступно. Можете выбрать Оплата или Менеджер.")
            _send_product_actions(runtime, notification)
            return True
        if already_sent:
            notification.answer("Видео отправил повторно. Если хотите, выберите оплату.")
        else:
            notification.answer("Видео отправил. Если хотите, выберите оплату.")
        _send_product_actions(runtime, notification)
        return True

    if runtime.sales_flow.is_buy_now_request(incoming_text):
        return _handle_order_request(runtime, notification, incoming_text)

    return False


def _handle_initial_discovery(runtime: BotRuntime, notification: Any, incoming_text: str) -> bool:
    state = runtime.sales_flow.get_state(notification.chat)
    if state.stage != "idle":
        return False

    language = runtime.sales_flow.remember_language(notification.chat, incoming_text)
    if runtime.catalog_service.find_best_product(incoming_text):
        return False

    if runtime.sales_flow.is_payment_intent(incoming_text):
        return False

    should_send_intro = runtime.sales_flow.is_greeting(
        incoming_text
    ) or runtime.sales_flow.is_catalog_browse_request(incoming_text)
    if not should_send_intro:
        return False

    notification.answer(
        runtime.sales_flow.build_initial_prompt(
            language,
            include_greeting=not state.intro_sent,
        )
    )
    runtime.sales_flow.mark_discovering(notification.chat)
    logger.info("Initial discovery prompt sent to %s", notification.chat)
    return True


def _handle_payment_method_selection(
    runtime: BotRuntime,
    notification: Any,
    incoming_text: str,
) -> bool:
    state = runtime.sales_flow.get_state(notification.chat)
    if state.stage != "awaiting_payment_method":
        return False

    language = runtime.sales_flow.remember_language(notification.chat, incoming_text)
    product = runtime.catalog_service.find_product_by_name(state.selected_product_name)
    payment_details = runtime.catalog_service.get_payment_details()
    if runtime.sales_flow.is_kaspi_selection(incoming_text):
        notification.answer(
            runtime.sales_flow.build_kaspi_details_message(product, state, language, payment_details)
        )
        if runtime.settings.payment_kaspi_qr_file:
            local_qr = _resolve_local_media_path(runtime, runtime.settings.payment_kaspi_qr_file)
            if local_qr:
                _try_send_media(runtime, notification, str(local_qr), "Kaspi QR")
            elif payment_details.kaspi_qr_url or runtime.settings.payment_kaspi_qr_url:
                _try_send_media(
                    runtime,
                    notification,
                    payment_details.kaspi_qr_url or runtime.settings.payment_kaspi_qr_url,
                    "Kaspi QR",
                )
        elif payment_details.kaspi_qr_url or runtime.settings.payment_kaspi_qr_url:
            _try_send_media(
                runtime,
                notification,
                payment_details.kaspi_qr_url or runtime.settings.payment_kaspi_qr_url,
                "Kaspi QR",
            )

        runtime.sales_flow.mark_payment_method(notification.chat, "kaspi")
        logger.info("Kaspi payment details sent to %s", notification.chat)
        return True

    if runtime.sales_flow.is_other_bank_selection(incoming_text):
        notification.answer(
            runtime.sales_flow.build_other_bank_details_message(product, state, language, payment_details)
        )
        runtime.sales_flow.mark_payment_method(notification.chat, "other_bank")
        logger.info("Other bank payment details sent to %s", notification.chat)
        return True

    return False


def _handle_customer_details_submission(
    runtime: BotRuntime,
    notification: Any,
    incoming_text: str,
    sender_name: str,
) -> bool:
    state = runtime.sales_flow.get_state(notification.chat)
    if state.stage != "awaiting_receipt":
        return False

    language = runtime.sales_flow.remember_language(notification.chat, incoming_text)
    full_name, phone, address = runtime.sales_flow.parse_customer_details(incoming_text)
    if not full_name and not phone and not address:
        return False

    runtime.sales_flow.save_customer_details(notification.chat, full_name, phone, address)
    if _finalize_receipt_if_ready(runtime, notification.chat, sender_name):
        product = runtime.catalog_service.find_product_by_name(state.selected_product_name)
        notification.answer(runtime.sales_flow.build_receipt_confirmation(product, language))
        logger.info("Receipt logged for %s after text details", notification.chat)
        return True

    notification.answer(runtime.sales_flow.build_waiting_receipt_message(language))
    logger.info("Customer details captured for %s, waiting for receipt", notification.chat)
    return True


def _build_ai_state_context(runtime: BotRuntime, chat_id: str) -> str:
    state = runtime.sales_flow.get_state(chat_id)
    selected_product = runtime.catalog_service.find_product_by_name(state.selected_product_name)
    product_line = selected_product.to_context_line() if selected_product else "none"
    return "\n".join(
        [
            "Current chat state:",
            f"- stage: {state.stage}",
            f"- intro_sent: {state.intro_sent}",
            f"- manager_mode: {state.manager_mode}",
            f"- selected_product: {state.selected_product_name or 'none'}",
            f"- selected_product_details: {product_line}",
            f"- selected_color: {state.order_color or 'none'}",
            f"- selected_quantity: {state.order_quantity or 'none'}",
            f"- customer_full_name: {state.customer_full_name or 'none'}",
            f"- customer_phone: {state.customer_phone or 'none'}",
            f"- delivery_address: {state.delivery_address or 'none'}",
            f"- remote_kaspi_phone: {state.remote_kaspi_phone or 'none'}",
            f"- payment_method: {state.payment_method or 'none'}",
            f"- follow_up_scheduled: {runtime.sales_flow.has_follow_up(chat_id)}",
            f"- follow_up_due_at: {runtime.sales_flow.get_follow_up_due_text(chat_id)}",
            "",
            "Routing rules:",
            "- Keep the current order state unless the customer clearly changes direction.",
            "- If the customer asks a side question during ordering, answer it briefly and keep the order stage in mind.",
            "- Do not restart the funnel when color, quantity, or customer details are already being collected.",
            "- If the customer already chose a product, keep using that product unless they clearly ask for another one.",
        ]
    )


def _resolve_product_for_decision(
    runtime: BotRuntime,
    chat_id: str,
    incoming_text: str,
    product_name: str,
):
    product = runtime.catalog_service.find_product_by_name(product_name)
    if product:
        return product

    state = runtime.sales_flow.get_state(chat_id)
    product = runtime.catalog_service.find_product_by_name(state.selected_product_name)
    if product:
        return product

    product = runtime.catalog_service.find_best_product(incoming_text)
    if product:
        return product

    return runtime.catalog_service.get_default_product()


def _record_assistant_action(runtime: BotRuntime, chat_id: str, decision: RouterDecision, fallback_text: str = "") -> None:
    text = decision.reply_text.strip() or fallback_text.strip()
    if text:
        runtime.ai_service.record_assistant_message(chat_id, text)


def _execute_router_decision(
    runtime: BotRuntime,
    notification: Any,
    sender_name: str,
    incoming_text: str,
    decision: RouterDecision,
) -> None:
    chat_id = notification.chat
    state = runtime.sales_flow.get_state(chat_id)
    product = _resolve_product_for_decision(runtime, chat_id, incoming_text, decision.product_name)
    fallback_reply = decision.reply_text or "Извините, не совсем понял. Могу показать фото, помочь с заказом или оплатой."

    if decision.action == "activate_manager":
        runtime.sales_flow.activate_manager_mode(chat_id)
        runtime.sales_flow.cancel_follow_up(chat_id)
        text = decision.reply_text or runtime.sales_flow.build_manager_handoff_message("ru")
        notification.answer(text)
        _record_assistant_action(runtime, chat_id, decision, text)
        logger.info("AI router activated manager mode for %s", chat_id)
        return

    if decision.action == "schedule_followup":
        delay_minutes = decision.follow_up_delay_minutes or 180
        reminder_text = decision.reminder_text or runtime.sales_flow.build_follow_up_reminder(product)
        text = decision.reply_text or runtime.sales_flow.build_follow_up_ack_message()
        runtime.sales_flow.schedule_follow_up(chat_id, reminder_text, delay_minutes)
        notification.answer(text)
        _record_assistant_action(runtime, chat_id, decision, text)
        logger.info("AI router scheduled follow-up for %s in %s minutes", chat_id, delay_minutes)
        return

    if decision.action == "showcase":
        if not product:
            notification.answer(fallback_reply)
            _record_assistant_action(runtime, chat_id, decision, fallback_reply)
            return

        primary_image = runtime.sales_flow.get_primary_image(product)
        media_sent = False
        if primary_image:
            media_sent = _try_send_media(
                runtime,
                notification,
                primary_image,
                decision.reply_text or runtime.sales_flow.build_offer_message(product, "ru"),
            )
        primary_video = runtime.sales_flow.get_primary_video(product)
        if primary_video:
            _try_send_media(runtime, notification, primary_video, None)

        offer_text = decision.reply_text or runtime.sales_flow.build_offer_message(product, "ru")
        if not media_sent:
            notification.answer(offer_text)
        _send_product_actions(runtime, notification)
        runtime.sales_flow.remember_product(chat_id, product)
        _record_assistant_action(runtime, chat_id, decision, offer_text)
        logger.info("AI router sent showcase to %s for %s", chat_id, product.name)
        return

    if decision.action == "ask_color":
        if not product:
            notification.answer(fallback_reply)
            _record_assistant_action(runtime, chat_id, decision, fallback_reply)
            return

        runtime.sales_flow.mark_waiting_order_selection(chat_id, product)
        body = decision.reply_text or runtime.sales_flow.build_order_selection_prompt(product)
        notification.answer(body)
        _record_assistant_action(runtime, chat_id, decision, body)
        logger.info("AI router requested combined color and quantity from %s", chat_id)
        return

    if decision.action == "ask_quantity":
        if not product:
            notification.answer(fallback_reply)
            _record_assistant_action(runtime, chat_id, decision, fallback_reply)
            return

        runtime.sales_flow.mark_waiting_order_selection(chat_id, product)
        color = decision.color or runtime.sales_flow.match_known_color(product, incoming_text)
        if color:
            runtime.sales_flow.save_order_details(chat_id, color, "")
        body = decision.reply_text or runtime.sales_flow.build_order_selection_prompt(product)
        notification.answer(body)
        _record_assistant_action(runtime, chat_id, decision, body)
        logger.info("AI router requested combined selection from %s", chat_id)
        return

    if decision.action == "ask_order_details":
        if not product:
            notification.answer(fallback_reply)
            _record_assistant_action(runtime, chat_id, decision, fallback_reply)
            return

        if decision.color:
            runtime.sales_flow.save_order_details(chat_id, decision.color, "")
        if decision.quantity:
            runtime.sales_flow.save_order_quantity(chat_id, decision.quantity)
        runtime.sales_flow.mark_waiting_order_address(chat_id)
        text = decision.reply_text or runtime.sales_flow.build_order_address_prompt()
        notification.answer(text)
        _record_assistant_action(runtime, chat_id, decision, text)
        logger.info("AI router requested customer details from %s", chat_id)
        return

    if decision.action == "show_payment_methods":
        if not product:
            notification.answer(fallback_reply)
            _record_assistant_action(runtime, chat_id, decision, fallback_reply)
            return

        full_name = decision.customer_full_name
        phone = decision.customer_phone
        address = decision.delivery_address
        if not (full_name and phone and address):
            parsed_name, parsed_phone, parsed_address = runtime.sales_flow.parse_customer_details(incoming_text)
            full_name = full_name or parsed_name
            phone = phone or parsed_phone
            address = address or parsed_address

        if not (full_name and phone and address):
            runtime.sales_flow.mark_waiting_order_address(chat_id)
            text = decision.reply_text or runtime.sales_flow.build_waiting_order_details_message()
            notification.answer(text)
            _record_assistant_action(runtime, chat_id, decision, text)
            logger.info("AI router still waiting for full customer details from %s", chat_id)
            return

        runtime.sales_flow.save_customer_details(chat_id, full_name, phone, address)
        runtime.sales_flow.mark_waiting_payment_method(chat_id, product)
        summary = runtime.sales_flow.build_order_summary(runtime.sales_flow.get_state(chat_id), product)
        if decision.reply_text:
            body = f"{decision.reply_text}\n\n{summary}\nВыберите способ оплаты:"
        else:
            body = f"{summary}\nВыберите способ оплаты:"
        notification.answer_with_interactive_buttons_reply(
            body=body,
            buttons=runtime.sales_flow.build_payment_buttons("ru"),
        )
        _record_assistant_action(runtime, chat_id, decision, body)
        logger.info("AI router requested payment method from %s", chat_id)
        return

    if decision.action == "show_payment_details":
        if not product:
            notification.answer(fallback_reply)
            _record_assistant_action(runtime, chat_id, decision, fallback_reply)
            return

        payment_method = (decision.payment_method or "").strip().lower()
        if not payment_method:
            if runtime.sales_flow.is_kaspi_selection(incoming_text):
                payment_method = "kaspi_qr"
            elif runtime.sales_flow.is_remote_selection(incoming_text):
                payment_method = "udalenka"

        if payment_method == "kaspi_qr":
            text = runtime.sales_flow.build_kaspi_details_message(product, state, "ru")
            local_qr_ref = _resolve_kaspi_qr_path(runtime)
            if local_qr_ref:
                _try_send_media(runtime, notification, local_qr_ref, text)
            elif runtime.settings.payment_kaspi_qr_url:
                _try_send_media(
                    runtime,
                    notification,
                    runtime.settings.payment_kaspi_qr_url,
                    text,
                )
            else:
                notification.answer(text)
            runtime.sales_flow.mark_payment_method(chat_id, "kaspi_qr")
            _record_assistant_action(runtime, chat_id, decision, text)
            logger.info("AI router sent Kaspi QR payment details to %s", chat_id)
            return

        if payment_method == "udalenka":
            runtime.sales_flow.mark_waiting_remote_kaspi_phone(chat_id, "udalenka")
            text = runtime.sales_flow.build_remote_kaspi_prompt(product, runtime.sales_flow.get_state(chat_id))
            notification.answer(text)
            _record_assistant_action(runtime, chat_id, decision, text)
            logger.info("AI router requested remote Kaspi phone from %s", chat_id)
            return

        if payment_method == "kaspi":
            reminder_text = "Выберите, пожалуйста, Kaspi QR или Удаленка."
            notification.answer(reminder_text)
            _record_assistant_action(runtime, chat_id, decision, reminder_text)
            return

        notification.answer(decision.reply_text or "Выберите, пожалуйста, Kaspi QR или Удаленка.")
        _record_assistant_action(runtime, chat_id, decision, decision.reply_text or "Выберите, пожалуйста, Kaspi QR или Удаленка.")
        return

    if decision.action == "send_more_photos":
        if not product:
            notification.answer(fallback_reply)
            _record_assistant_action(runtime, chat_id, decision, fallback_reply)
            return

        next_image, exhausted = runtime.sales_flow.get_next_image(chat_id, product)
        if not next_image:
            text = decision.reply_text or "Дополнительные фото закончились. Могу помочь с заказом или оплатой."
            notification.answer(text)
            _record_assistant_action(runtime, chat_id, decision, text)
            return

        _try_send_media(runtime, notification, next_image, None)
        text = decision.reply_text or ("Это было последнее фото." if exhausted else "Отправил еще фото.")
        notification.answer(text)
        _send_product_actions(runtime, notification)
        _record_assistant_action(runtime, chat_id, decision, text)
        logger.info("AI router sent more photos to %s", chat_id)
        return

    if decision.action == "send_color_photos":
        if not product:
            notification.answer(fallback_reply)
            _record_assistant_action(runtime, chat_id, decision, fallback_reply)
            return

        color_name = decision.color or runtime.sales_flow.match_known_color(product, incoming_text)
        if not color_name:
            text = decision.reply_text or "Уточните, пожалуйста, какой именно цвет вам показать."
            notification.answer(text)
            _record_assistant_action(runtime, chat_id, decision, text)
            return

        color_refs = runtime.catalog_service.get_color_images(product, color_name)
        if not color_refs:
            text = decision.reply_text or f"По цвету {color_name} отдельные фото пока не добавлены."
            notification.answer(text)
            _record_assistant_action(runtime, chat_id, decision, text)
            return

        first_sent = False
        for index, ref in enumerate(color_refs):
            caption = decision.reply_text if index == 0 else None
            if _try_send_media(runtime, notification, ref, caption):
                first_sent = True

        if not first_sent:
            text = decision.reply_text or f"Пока не получилось отправить фото в цвете {color_name}."
            notification.answer(text)
            _record_assistant_action(runtime, chat_id, decision, text)
            return

        runtime.ai_service.record_assistant_message(
            chat_id,
            decision.reply_text or f"Отправляю фото в цвете {color_name}.",
        )
        logger.info("AI router sent color photos to %s for %s", chat_id, color_name)
        return

    if decision.action == "send_video":
        if not product:
            notification.answer(fallback_reply)
            _record_assistant_action(runtime, chat_id, decision, fallback_reply)
            return

        video_ref, already_sent = runtime.sales_flow.get_video(chat_id, product)
        if not video_ref:
            text = decision.reply_text or "По этой модели видео пока нет."
            notification.answer(text)
            _record_assistant_action(runtime, chat_id, decision, text)
            return

        _try_send_media(runtime, notification, video_ref, None)
        text = decision.reply_text or ("Видео отправил повторно." if already_sent else "Видео отправил.")
        notification.answer(text)
        _send_product_actions(runtime, notification)
        _record_assistant_action(runtime, chat_id, decision, text)
        logger.info("AI router sent video to %s", chat_id)
        return

    text = decision.reply_text or fallback_reply
    notification.answer(text)
    _record_assistant_action(runtime, chat_id, decision, text)
    logger.info("AI router sent reply to %s", chat_id)


def handle_text_notification(runtime: BotRuntime, notification: Any) -> None:
    chat_id = notification.chat
    if runtime.settings.ignore_group_chats and is_group_chat(chat_id):
        logger.info("Skipping group chat message from %s", chat_id)
        return

    message_id = get_message_id(notification.event)
    if message_id and runtime.sales_flow.mark_message_processed(message_id):
        logger.info("Skipping duplicate text message %s from %s", message_id, chat_id)
        return

    incoming_text = extract_message_text(notification.event)
    if not incoming_text:
        logger.info(
            "Skipping empty text message from %s, type=%s, keys=%s",
            chat_id,
            get_message_type(notification.event),
            list(notification.event.get("messageData", {}).keys()),
        )
        return

    if incoming_text.startswith("/start") or incoming_text.startswith("/clear"):
        return

    if runtime.sales_flow.cancel_follow_up(chat_id):
        logger.info("Canceled pending follow-up because customer replied in %s", chat_id)

    if runtime.sales_flow.is_manager_mode(chat_id):
        logger.info("Skipping bot reply because manager mode is active for %s", chat_id)
        return

    sender_name = get_sender_name(notification.event)
    logger.info("Incoming message from %s: %s", chat_id, incoming_text)

    try:
        if _handle_customer_details_submission(runtime, notification, incoming_text, sender_name):
            return

        state = runtime.sales_flow.get_state(chat_id)
        if state.stage == "awaiting_remote_kaspi_phone":
            kaspi_phone = runtime.sales_flow.parse_kaspi_phone(incoming_text)
            if not kaspi_phone:
                notification.answer("Отправьте, пожалуйста, номер Kaspi в формате 8707XXXXXXX.")
                return

            runtime.sales_flow.save_remote_kaspi_phone(chat_id, kaspi_phone)
            runtime.sales_flow.mark_waiting_remote_status(chat_id)
            product = runtime.catalog_service.find_product_by_name(state.selected_product_name)
            body = runtime.sales_flow.build_remote_kaspi_waiting_message(
                product,
                runtime.sales_flow.get_state(chat_id),
            )
            notification.answer_with_interactive_buttons_reply(
                body=body,
                buttons=runtime.sales_flow.build_remote_status_buttons(),
            )
            runtime.ai_service.record_assistant_message(chat_id, body)
            logger.info("Remote Kaspi phone captured for %s", chat_id)
            return

        if state.stage == "awaiting_remote_status":
            if runtime.sales_flow.is_remote_paid_selection(incoming_text):
                confirmation_text = (
                    "Спасибо! Отметили, что вы оплатили.\n"
                    "Сейчас проверяем поступление оплаты, и менеджер свяжется с вами в ближайшее время."
                )
                try:
                    _finalize_remote_status(runtime, chat_id, sender_name, "paid")
                except Exception:
                    logger.exception("Failed to save remote paid status for %s", chat_id)
                notification.answer(confirmation_text)
                runtime.ai_service.record_assistant_message(chat_id, confirmation_text)
                logger.info("Remote payment marked paid for %s", chat_id)
                return
            if runtime.sales_flow.is_remote_declined_selection(incoming_text):
                decline_text = "Хорошо, отметили отказ. Если передумаете, мы всегда на связи."
                try:
                    _finalize_remote_status(runtime, chat_id, sender_name, "declined")
                except Exception:
                    logger.exception("Failed to save remote declined status for %s", chat_id)
                notification.answer(decline_text)
                runtime.ai_service.record_assistant_message(chat_id, decline_text)
                logger.info("Remote payment marked declined for %s", chat_id)
                return

            notification.answer("Пожалуйста, выберите один из вариантов: Оплатил или Отказ.")
            return

        if _handle_universal_template(runtime, notification, incoming_text):
            return

        runtime.ai_service.record_user_message(chat_id, sender_name, incoming_text)
        state_context = _build_ai_state_context(runtime, chat_id)
        catalog_text = _build_selected_product_catalog_text(runtime, chat_id, incoming_text)
        decision = runtime.ai_service.route_message(
            chat_id=chat_id,
            user_name=sender_name,
            message_text=incoming_text,
            state_context=state_context,
            catalog_text=catalog_text,
        )
    except Exception:
        logger.exception("Failed to generate a reply for %s", chat_id)
        notification.answer("Извините, сейчас не получается получить каталог или ответ AI. Напишите чуть позже.")
        return

    try:
        _execute_router_decision(runtime, notification, sender_name, incoming_text, decision)
    except Exception:
        logger.exception("Failed to execute AI router decision for %s", chat_id)
        notification.answer("Извините, сейчас не получилось обработать запрос. Напишите еще раз.")


def handle_receipt_notification(runtime: BotRuntime, notification: Any) -> None:
    chat_id = notification.chat
    if runtime.settings.ignore_group_chats and is_group_chat(chat_id):
        return

    message_id = get_message_id(notification.event)
    if message_id and runtime.sales_flow.mark_message_processed(message_id):
        logger.info("Skipping duplicate receipt message %s from %s", message_id, chat_id)
        return

    if runtime.sales_flow.cancel_follow_up(chat_id):
        logger.info("Canceled pending follow-up because receipt arrived in %s", chat_id)

    if runtime.sales_flow.is_manager_mode(chat_id):
        logger.info("Skipping receipt handler because manager mode is active for %s", chat_id)
        return

    sender_name = get_sender_name(notification.event)
    message_type = get_message_type(notification.event)
    logger.info("Incoming %s from %s", message_type, chat_id)

    state = runtime.sales_flow.get_state(chat_id)
    if state.stage != "awaiting_receipt":
        notification.answer("Файл получили. Если это чек оплаты, сначала напишите, что хотите оформить оплату.")
        return

    product = runtime.catalog_service.find_product_by_name(state.selected_product_name)
    receipt_info = extract_file_info(notification.event)
    full_name, phone, address = runtime.sales_flow.parse_customer_details(receipt_info.get("caption", ""))
    if full_name or phone or address:
        runtime.sales_flow.save_customer_details(chat_id, full_name, phone, address)
    runtime.sales_flow.save_pending_receipt(chat_id, receipt_info)

    try:
        if _finalize_receipt_if_ready(runtime, chat_id, sender_name):
            notification.answer(
                runtime.sales_flow.build_receipt_confirmation(
                    product,
                    runtime.sales_flow.get_language(chat_id),
                )
            )
            logger.info("Receipt logged for %s", chat_id)
        else:
            notification.answer(
                runtime.sales_flow.build_waiting_customer_details_message(
                    runtime.sales_flow.get_language(chat_id)
                )
            )
            logger.info("Receipt captured for %s, waiting for customer details", chat_id)
    except Exception:
        logger.exception("Failed to log receipt for %s", chat_id)
        notification.answer("Чек получили, но при записи в базу произошла ошибка. Попробуйте отправить еще раз.")


def handle_manual_outgoing(runtime: BotRuntime, notification: Any) -> None:
    chat_id = notification.chat
    if not chat_id:
        return

    if notification.event.get("typeWebhook") != "outgoingMessageReceived":
        return

    if runtime.settings.ignore_group_chats and is_group_chat(chat_id):
        return

    runtime.sales_flow.cancel_follow_up(chat_id)
    runtime.sales_flow.activate_manager_mode(chat_id)
    logger.info("Manager mode activated by manual outgoing message for %s", chat_id)
