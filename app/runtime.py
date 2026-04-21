from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from app.ai_service import OpenAIService
from app.catalog_service import CatalogService
from app.config import Settings, load_settings
from app.payment_logger import PaymentLogger
from app.sales_flow import SalesFlowService


@dataclass(frozen=True)
class BotRuntime:
    settings: Settings
    ai_service: OpenAIService
    catalog_service: CatalogService
    payment_logger: PaymentLogger
    sales_flow: SalesFlowService
    project_root: Path


def create_runtime(project_root: Path) -> BotRuntime:
    settings = load_settings()
    railway_volume_mount = os.getenv("RAILWAY_VOLUME_MOUNT_PATH", "").strip()
    if railway_volume_mount:
        state_dir = Path(railway_volume_mount)
    else:
        state_dir = project_root / "data"

    state_store_path = state_dir / "chat_state.json"
    history_store_path = state_dir / "ai_history.json"

    sales_flow = SalesFlowService(settings, state_store_path=state_store_path)
    return BotRuntime(
        settings=settings,
        ai_service=OpenAIService(settings, history_store_path=history_store_path),
        catalog_service=CatalogService(settings),
        payment_logger=PaymentLogger(settings, sales_flow),
        sales_flow=sales_flow,
        project_root=project_root,
    )
