from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.catalog_service import Product
from app.config import Settings
from app.sales_flow import SalesFlowService


class PaymentLogger:
    def __init__(self, settings: Settings, sales_flow: SalesFlowService | None = None) -> None:
        self._settings = settings
        self._sales_flow = sales_flow

    def log_client_status(
        self,
        chat_id: str,
        whatsapp_name: str,
        customer_full_name: str,
        customer_phone: str,
        delivery_address: str,
        order_color: str,
        order_quantity: str,
        product: Product | None,
        payment_method: str,
        status: str,
        receipt_info: dict[str, str] | None = None,
        remote_kaspi_phone: str = "",
    ) -> dict[str, Any]:
        receipt_info = receipt_info or {}
        payload = {
            "action": "log_client",
            "secret": self._settings.google_apps_script_secret,
            "clientsSheetName": self._settings.clients_sheet_name,
            "chatId": chat_id,
            "whatsAppName": whatsapp_name,
            "customerFullName": customer_full_name,
            "customerPhone": customer_phone,
            "deliveryAddress": delivery_address,
            "productName": product.name if product else "",
            "orderColor": order_color,
            "orderQuantity": order_quantity,
            "remoteKaspiPhone": remote_kaspi_phone,
            "amount": (
                self._sales_flow.calculate_total_price_numeric(product, order_quantity)
                if self._sales_flow
                else (product.current_price if product else "")
            ),
            "status": status,
            "paymentMethod": payment_method,
            "receiptType": receipt_info.get("type", ""),
            "receiptUrl": receipt_info.get("downloadUrl", ""),
            "receiptCaption": receipt_info.get("caption", ""),
            "receiptFileName": receipt_info.get("fileName", ""),
            "notes": receipt_info.get("mimeType", ""),
        }

        request = Request(
            self._settings.google_apps_script_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urlopen(request, timeout=20) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as error:
            raise RuntimeError(f"Payment log API returned HTTP {error.code}.") from error
        except URLError as error:
            raise RuntimeError("Payment log API is unreachable.") from error

        try:
            result = json.loads(raw)
        except json.JSONDecodeError as error:
            snippet = raw[:300].replace("\n", " ").strip()
            raise RuntimeError(f"Payment log API did not return valid JSON. Raw: {snippet}") from error

        if not result.get("ok"):
            raise RuntimeError(f"Payment log API error: {result.get('error', 'unknown error')}")

        return result

    def log_receipt(
        self,
        chat_id: str,
        whatsapp_name: str,
        customer_full_name: str,
        customer_phone: str,
        delivery_address: str,
        order_color: str,
        order_quantity: str,
        product: Product | None,
        payment_method: str,
        receipt_info: dict[str, str],
    ) -> dict[str, Any]:
        return self.log_client_status(
            chat_id=chat_id,
            whatsapp_name=whatsapp_name,
            customer_full_name=customer_full_name,
            customer_phone=customer_phone,
            delivery_address=delivery_address,
            order_color=order_color,
            order_quantity=order_quantity,
            product=product,
            payment_method=payment_method,
            status="paid",
            receipt_info=receipt_info,
        )
