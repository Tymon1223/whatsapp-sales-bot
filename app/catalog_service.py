from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.config import Settings


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Product:
    name: str
    sku: str = ""
    category: str = ""
    price: str = ""
    regular_price: str = ""
    sale_price: str = ""
    kaspi_installment: str = ""
    currency: str = ""
    stock: str = ""
    colors: str = ""
    sizes: str = ""
    dimensions: str = ""
    description: str = ""
    notes: str = ""
    delivery_fee: str = ""
    delivery_time: str = ""
    link: str = ""
    image_refs: tuple[str, ...] = field(default_factory=tuple)
    video_refs: tuple[str, ...] = field(default_factory=tuple)
    color_image_refs: dict[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def current_price(self) -> str:
        return self.sale_price or self.price or self.regular_price

    def to_context_line(self) -> str:
        parts = []
        for label, value in (
            ("price", self.price),
            ("regular price", self.regular_price),
            ("sale price", self.sale_price),
            ("kaspi installment", self.kaspi_installment),
            ("stock", self.stock),
            ("available colors", self.colors),
            ("sizes", self.sizes),
            ("dimensions", self.dimensions),
            ("description", self.description),
            ("notes", self.notes),
            ("delivery fee", self.delivery_fee),
            ("delivery time", self.delivery_time),
            ("link", self.link),
        ):
            if value:
                parts.append(f"{label}: {value}")
        if self.color_image_refs:
            parts.append("color photos available: yes")

        if not parts:
            parts.append("details: no extra fields filled")

        return f"- {self.name} | " + " | ".join(parts)


@dataclass(frozen=True)
class PaymentDetails:
    kaspi_number: str = ""
    other_bank_card: str = ""
    recipient_name: str = ""
    kaspi_qr_url: str = ""


@dataclass
class CatalogSnapshot:
    products: list[Product]
    payment_details: PaymentDetails
    loaded_at: float


class CatalogService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._snapshot: CatalogSnapshot | None = None
        self._products_dir = Path(__file__).resolve().parents[1] / "assets" / "products"

    def get_catalog_text(self, query: str) -> str:
        products = self.search_products(query)
        if not products:
            return (
                "Catalog status: unavailable.\n"
                "The Google Sheet returned no usable product rows.\n"
                "Do not invent product information."
            )

        return "\n".join(
            [
                "Catalog status: available.",
                "Use only the products and facts listed below when answering product questions.",
                "If a requested product or fact is not listed here, say it is not available in the catalog yet.",
                "Relevant catalog entries:",
                *[product.to_context_line() for product in products],
            ]
        )

    def search_products(self, query: str, limit: int = 3) -> list[Product]:
        products = self._get_snapshot().products
        if not products:
            return []

        query_terms = self._tokenize(query)
        if not query_terms:
            return []

        scored: list[tuple[int, Product]] = []
        for product in products:
            haystack = " ".join(
                [
                    product.name,
                    product.category,
                    product.description,
                    product.notes,
                    product.colors,
                    product.dimensions,
                    product.sizes,
                ]
            )
            score = len(query_terms & self._tokenize(haystack))
            if query_terms and score == 0:
                continue
            scored.append((score, product))

        if not scored:
            return []

        scored.sort(key=lambda item: item[0], reverse=True)
        return [product for _, product in scored[:limit]]

    def find_best_product(self, query: str) -> Product | None:
        products = self.search_products(query, limit=1)
        return products[0] if products else None

    def get_default_product(self) -> Product | None:
        products = self._get_snapshot().products
        return products[0] if products else None

    def get_payment_details(self) -> PaymentDetails:
        return self._get_snapshot().payment_details

    def find_product_by_name(self, product_name: str | None) -> Product | None:
        if not product_name:
            return None

        expected = product_name.strip().lower()
        if not expected:
            return None

        for product in self._get_snapshot().products:
            if product.name.strip().lower() == expected:
                return product

        return None

    def _get_snapshot(self) -> CatalogSnapshot:
        if self._snapshot and not self._is_stale(self._snapshot.loaded_at):
            return self._snapshot

        self._snapshot = self._load_snapshot()
        return self._snapshot

    def _is_stale(self, loaded_at: float) -> bool:
        return (time.time() - loaded_at) >= self._settings.catalog_refresh_seconds

    def _load_snapshot(self) -> CatalogSnapshot:
        request = Request(
            self._settings.google_apps_script_url,
            headers={"Accept": "application/json"},
            method="GET",
        )

        try:
            with urlopen(request, timeout=15) as response:
                payload = response.read().decode("utf-8")
        except HTTPError as error:
            raise RuntimeError(f"Catalog API returned HTTP {error.code}.") from error
        except URLError as error:
            raise RuntimeError("Catalog API is unreachable.") from error

        try:
            data = json.loads(payload)
        except json.JSONDecodeError as error:
            raise RuntimeError("Catalog API did not return valid JSON.") from error

        products = self._extract_products(data)
        payment_details = self._extract_payment_details(data)
        logger.info("Loaded %s catalog row(s) from Apps Script API", len(products))
        return CatalogSnapshot(
            products=products,
            payment_details=payment_details,
            loaded_at=time.time(),
        )

    def _extract_products(self, payload: object) -> list[Product]:
        records = self._normalize_payload(payload)
        products: list[Product] = []

        for record in records:
            normalized = {
                self._normalize_key(key): str(value).strip()
                for key, value in record.items()
                if value is not None and str(value).strip()
            }

            name = self._pick(
                normalized,
                "name",
                "product",
                "title",
                "product name",
                "product_name",
            )
            if not name:
                continue

            product = Product(
                name=name,
                sku=self._pick(normalized, "sku", "article"),
                category=self._pick(normalized, "category"),
                price=self._pick(normalized, "price"),
                regular_price=self._pick(normalized, "regular price", "old price"),
                sale_price=self._pick(normalized, "sale price", "discount price"),
                kaspi_installment=self._pick(normalized, "kaspi installment"),
                currency=self._pick(normalized, "currency"),
                stock=self._pick(normalized, "stock", "availability"),
                colors=self._pick(normalized, "available colors", "colors", "color"),
                sizes=self._pick(normalized, "sizes", "size"),
                dimensions=self._pick(normalized, "dimensions (size)", "dimensions"),
                description=self._pick(normalized, "main description", "description"),
                notes=self._pick(normalized, "notes"),
                delivery_fee=self._pick(normalized, "delivery fee"),
                delivery_time=self._pick(normalized, "delivery time"),
                link=self._pick(normalized, "link", "url"),
                image_refs=tuple(
                    self._collect_media_refs(
                        normalized,
                        (
                            "photo url 1",
                            "photo url 2",
                            "photo url 3",
                            "image url 1",
                            "image url 2",
                            "image url 3",
                            "photo",
                            "photos",
                            "image",
                            "images",
                        ),
                    )
                ),
                video_refs=tuple(
                    self._collect_media_refs(
                        normalized,
                        (
                            "video url",
                            "video url 1",
                            "video url 2",
                            "video",
                            "videos",
                        ),
                    )
                ),
                color_image_refs=self._extract_color_image_refs(normalized),
            )
            product = self._attach_local_media_defaults(product)
            products.append(product)

        return products

    def _normalize_payload(self, payload: object) -> list[dict[str, object]]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]

        if isinstance(payload, dict):
            products = payload.get("products")
            if isinstance(products, list):
                return [item for item in products if isinstance(item, dict)]

        return []

    def _extract_payment_details(self, payload: object) -> PaymentDetails:
        if not isinstance(payload, dict):
            return PaymentDetails()

        raw = payload.get("paymentDetails")
        if not isinstance(raw, dict):
            return PaymentDetails()

        normalized = {
            self._normalize_key(key): str(value).strip()
            for key, value in raw.items()
            if value is not None and str(value).strip()
        }
        return PaymentDetails(
            kaspi_number=self._pick(
                normalized,
                "kaspi number",
                "payment transfer number",
                "payment_transfer_number",
                "kaspi_number",
            ),
            other_bank_card=self._pick(
                normalized,
                "other bank card",
                "payment other bank card",
                "payment_other_bank_card",
                "card number",
            ),
            recipient_name=self._pick(
                normalized,
                "recipient name",
                "payment transfer name",
                "payment_transfer_name",
                "receiver name",
            ),
            kaspi_qr_url=self._pick(
                normalized,
                "kaspi qr url",
                "payment kaspi qr url",
                "payment_kaspi_qr_url",
            ),
        )

    def _pick(self, values: dict[str, str], *keys: str) -> str:
        for key in keys:
            normalized_key = self._normalize_key(key)
            value = values.get(normalized_key, "")
            if value:
                return value
        return ""

    def _collect_media_refs(self, values: dict[str, str], keys: tuple[str, ...]) -> list[str]:
        urls: list[str] = []
        for key in keys:
            normalized_key = self._normalize_key(key)
            value = values.get(normalized_key, "")
            if value:
                urls.extend(self._split_media_refs(value))
        return list(dict.fromkeys(urls))

    def _extract_color_image_refs(self, values: dict[str, str]) -> dict[str, tuple[str, ...]]:
        color_map: dict[str, tuple[str, ...]] = {}
        for key, value in values.items():
            if not value:
                continue

            normalized_key = self._normalize_key(key)
            color_name = ""
            if normalized_key.startswith("photos "):
                color_name = normalized_key.replace("photos ", "", 1)
            elif normalized_key.endswith(" photos"):
                color_name = normalized_key[: -len(" photos")]
            elif normalized_key.startswith("color photos "):
                color_name = normalized_key.replace("color photos ", "", 1)

            if not color_name:
                continue

            refs = tuple(self._split_media_refs(value))
            if refs:
                color_map[color_name] = refs

        return color_map

    def _split_media_refs(self, value: str) -> list[str]:
        parts = re.split(r"[,\n;]+", value)
        return [part.strip() for part in parts if part.strip()]

    def _normalize_key(self, value: object) -> str:
        return re.sub(r"\s+", " ", str(value).strip().lower())

    def _tokenize(self, text: str) -> set[str]:
        return {
            token
            for token in re.findall(r"[a-zA-Zа-яА-Я0-9_-]+", text.lower())
            if len(token) >= 2
        }

    def _attach_local_media_defaults(self, product: Product) -> Product:
        if not self._products_dir.exists():
            return product

        image_refs = list(product.image_refs)
        video_refs = list(product.video_refs)

        main_image = self._find_first_existing(("main.jpeg", "main.jpg", "main.png", "main.webp"))
        main_video = self._find_first_existing(("main2.mov", "main2.mp4", "main2.m4v", "main2.avi"))

        extra_images = [
            path.name
            for path in sorted(self._products_dir.iterdir(), key=lambda item: item.name.lower())
            if path.is_file()
            and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
            and path.name.lower() not in {"main.jpeg", "main.jpg", "main.png", "main.webp", ".gitkeep"}
        ]

        merged_images: list[str] = []
        if main_image:
            merged_images.append(main_image)
        merged_images.extend(image_refs)
        merged_images.extend(extra_images)

        merged_videos: list[str] = []
        if main_video:
            merged_videos.append(main_video)
        merged_videos.extend(video_refs)

        return Product(
            name=product.name,
            sku=product.sku,
            category=product.category,
            price=product.price,
            regular_price=product.regular_price,
            sale_price=product.sale_price,
            kaspi_installment=product.kaspi_installment,
            currency=product.currency,
            stock=product.stock,
            colors=product.colors,
            sizes=product.sizes,
            dimensions=product.dimensions,
            description=product.description,
            notes=product.notes,
            delivery_fee=product.delivery_fee,
            delivery_time=product.delivery_time,
            link=product.link,
            image_refs=tuple(dict.fromkeys(merged_images)),
            video_refs=tuple(dict.fromkeys(merged_videos)),
            color_image_refs=product.color_image_refs,
        )

    def get_color_images(self, product: Product, color_name: str) -> tuple[str, ...]:
        normalized_color = self._normalize_color(color_name)
        for key, refs in product.color_image_refs.items():
            if self._normalize_color(key) == normalized_color:
                return refs

        token = self._canonical_color_token(color_name)
        if not token:
            return ()

        local_refs = self._find_local_color_images(token)
        return tuple(local_refs)

    def _normalize_color(self, value: str) -> str:
        return re.sub(r"\s+", " ", value.strip().lower())

    def _canonical_color_token(self, color_name: str) -> str:
        normalized = self._normalize_color(color_name)
        aliases = {
            "white": {
                "white",
                "белый",
                "белая",
                "ақ",
                "ак",
                "ақ түсті",
                "ак тусти",
            },
            "black": {
                "black",
                "черный",
                "чёрный",
                "қара",
                "кара",
                "қара түсті",
                "кара тусти",
            },
            "beige": {
                "beige",
                "бежевый",
                "беж",
                "бежевый цвет",
                "беж",
            },
            "pink": {
                "pink",
                "розовый",
                "розовый цвет",
                "қызғылт",
                "кызгылт",
                "роз",
            },
            "gray": {
                "gray",
                "grey",
                "серый",
                "сұр",
                "сур",
                "graphite",
                "графит",
            },
            "blue": {
                "blue",
                "синий",
                "көк",
                "кок",
            },
            "light_blue": {
                "light blue",
                "lightblue",
                "голубой",
                "небесно голубой",
                "бирюзовый",
                "бирюза",
                "turquoise",
                "ашық көк",
                "ашык кок",
            },
            "brown": {
                "brown",
                "коричневый",
                "шоколадный",
                "темно-коричневый",
                "тёмно-коричневый",
                "қоңыр",
                "коныр",
            },
            "red": {
                "red",
                "красный",
                "қызыл",
                "кызыл",
            },
            "cream": {
                "cream",
                "creamy",
                "молочный",
                "кремовый",
                "айвори",
                "ivory",
            },
        }
        for token, values in aliases.items():
            if normalized in values:
                return token
        return ""

    def _find_local_color_images(self, token: str) -> list[str]:
        if not self._products_dir.exists():
            return []

        candidates: list[str] = []
        color_dirs = [
            self._products_dir / "colors" / token,
            self._products_dir / token,
        ]
        for directory in color_dirs:
            if directory.exists() and directory.is_dir():
                for path in sorted(directory.iterdir(), key=lambda item: item.name.lower()):
                    if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}:
                        candidates.append(str(path))

        if candidates:
            return candidates

        prefixes = (
            f"{token}_",
            f"{token}-",
            f"{token} ",
        )
        for path in sorted(self._products_dir.iterdir(), key=lambda item: item.name.lower()):
            if not path.is_file():
                continue
            if path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
                continue
            lowered = path.name.lower()
            if lowered in {"main.jpeg", "main.jpg", "main.png", "main.webp", ".gitkeep"}:
                continue
            if any(lowered.startswith(prefix) for prefix in prefixes):
                candidates.append(path.name)

        return candidates

    def _find_first_existing(self, names: tuple[str, ...]) -> str:
        available = {path.name.lower(): path.name for path in self._products_dir.iterdir() if path.is_file()}
        for name in names:
            match = available.get(name.lower())
            if match:
                return match
        return ""
