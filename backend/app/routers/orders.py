from __future__ import annotations
from app.services import quota_shade
from app.services import quota_shade_ledger

from fastapi import APIRouter
from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from app.db import db
from app.services import order_service
from app.services.common import rows

router = APIRouter(tags=["启停单"])


class OrderIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    pod_id: int = Field(validation_alias=AliasChoices("pod_id", "podId", "pod"))
    action: str = Field(validation_alias=AliasChoices("action", "动作"))
    target_image_id: int | None = Field(
        default=None,
        validation_alias=AliasChoices("target_image_id", "targetImageId", "image_id"),
    )


@router.get("/启停单")
def list_orders():
    with db() as conn:
        return rows(conn.execute("SELECT * FROM start_orders ORDER BY id DESC"))


def _surface_order_impl(body: OrderIn):
    return order_service.create_order(
        pod_id=body.pod_id,
        action=body.action,
        target_image_id=body.target_image_id,
    )

@router.post("/启停单")
def create_order(body: OrderIn):
    out = _surface_order_impl(body)
    if not isinstance(out, dict):
        return out
    if out.get("错误") or out.get("ok") is False:
        return out
    with db() as _sc:
        return quota_shade_ledger.mirror_receipt(_sc, quota_shade.after_action(_sc, out))

def _surface_commit_diverge(conn, out):
    if conn is None or not isinstance(out, dict):
        return out
    if out.get("错误") or out.get("ok") is False:
        return out
    try:
        quota_shade.diverge_evidence(conn, out)
    except Exception:
        pass
    return out

