from __future__ import annotations

from typing import Any


def _first_text_value(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _extract_reply_text(reply_data: dict[str, Any]) -> str:
    if not isinstance(reply_data, dict):
        return ""

    button_text = reply_data.get("buttonText")
    selected_reply = reply_data.get("selectedReply") or {}
    single_select_reply = reply_data.get("singleSelectReply") or {}

    if isinstance(button_text, dict):
        button_text = (
            button_text.get("displayText")
            or button_text.get("text")
            or button_text.get("selectedDisplayText")
        )

    if isinstance(selected_reply, dict):
        selected_reply = (
            selected_reply.get("displayText")
            or selected_reply.get("title")
            or selected_reply.get("id")
        )

    if isinstance(single_select_reply, dict):
        single_select_reply = (
            single_select_reply.get("selectedRowId")
            or single_select_reply.get("title")
        )

    return _first_text_value(
        reply_data.get("selectedDisplayText"),
        reply_data.get("displayText"),
        reply_data.get("selectedText"),
        reply_data.get("text"),
        reply_data.get("title"),
        reply_data.get("selectedId"),
        reply_data.get("buttonId"),
        reply_data.get("selectedButtonId"),
        button_text,
        selected_reply,
        single_select_reply,
    )


def get_message_type(event: dict[str, Any]) -> str:
    message_data = event.get("messageData", {})
    return str(message_data.get("typeMessage", ""))


def get_message_id(event: dict[str, Any]) -> str:
    return str(event.get("idMessage", "")).strip()


def get_sender_name(event: dict[str, Any]) -> str:
    sender_data = event.get("senderData", {})
    return str(sender_data.get("senderName") or sender_data.get("chatName") or "Customer")


def is_group_chat(chat_id: str) -> bool:
    return chat_id.endswith("@g.us")


def extract_message_text(event: dict[str, Any]) -> str:
    message_data = event.get("messageData", {})
    message_type = message_data.get("typeMessage")

    if message_type == "textMessage":
        text = message_data.get("textMessageData", {}).get("textMessage", "")
        return str(text).strip()

    if message_type == "extendedTextMessage":
        text = message_data.get("extendedTextMessageData", {}).get("text", "")
        return str(text).strip()

    if message_type in (
        "templateButtonsReplyMessage",
        "interactiveButtonsResponse",
        "buttonsResponseMessage",
    ):
        reply_data = (
            message_data.get("templateButtonReplyMessage")
            or message_data.get("buttonsResponseMessage")
            or message_data.get("interactiveButtonsResponse")
            or {}
        )
        return _extract_reply_text(reply_data)

    if message_type in ("interactiveButtonsReply", "interactiveButtonReply"):
        reply_data = (
            message_data.get("interactiveButtonsReply")
            or message_data.get("interactiveButtonReply")
            or message_data.get("buttonsResponseMessage")
            or {}
        )
        return _extract_reply_text(reply_data)

    if message_type == "listResponseMessage":
        reply_data = message_data.get("listResponseMessage", {})
        return _extract_reply_text(reply_data)

    return ""


def extract_file_info(event: dict[str, Any]) -> dict[str, str]:
    message_data = event.get("messageData", {})
    file_data = (
        message_data.get("fileMessageData")
        or message_data.get("imageMessageData")
        or message_data.get("documentMessageData")
        or {}
    )
    return {
        "type": str(message_data.get("typeMessage", "")),
        "downloadUrl": str(file_data.get("downloadUrl", "")),
        "caption": str(file_data.get("caption", "")),
        "fileName": str(file_data.get("fileName", "")),
        "mimeType": str(file_data.get("mimeType", "")),
    }
