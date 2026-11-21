"""业务动作接口：字段映射校验明细、目标列冲突校验与批次结算。

资源路由仍按模块 key 分派列表与状态流转；本路由补充需要读取原始
记录才能完成的校验和结算动作，前端不直接拼接业务字段。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from app.schemas import ActionResult
from app.services.data_import_rule import DataImportRuleService
from app.services.import_batch import ImportBatchService
from app.store import store

router = APIRouter(prefix="/api", tags=["业务校验"])


def _payload_values(payload: dict[str, Any] | None) -> dict[str, Any]:
    values = (payload or {}).get("values")
    return values if isinstance(values, dict) else {}


def _payload_records(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    data = payload or {}
    records = data.get("records") or data.get("rows") or data.get("raw_data") or []
    if not isinstance(records, list):
        raise HTTPException(status_code=422, detail="records 必须是原始记录列表")
    return [record for record in records if isinstance(record, dict)]


def _require_entry(module: str, entry_id: int) -> dict[str, Any]:
    entry = store.find(module, entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"{module} {entry_id} 不存在或已归档")
    return entry


@router.post("/data_import_rule/{rule_id}/field-mapping-validation",
             response_model=dict)
def validate_field_mapping(rule_id: int, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """生成字段映射校验明细：逐行核对写入同一目标列的源字段值。

    请求体可传 ``records``（原始行），以及可选的 ``target_column``、
    ``precedence``（来源列优先级）。返回值中的 ``verified_scope``
    即本次核实过的输入行数，``row_results`` 每条原始行只出现一次。
    """
    _require_entry(DataImportRuleService.MODULE, rule_id)
    data = payload or {}
    service = DataImportRuleService()
    options: dict[str, Any] = {}
    if data.get("target_column"):
        options["target_column"] = str(data["target_column"])
    if isinstance(data.get("precedence"), (dict, list)):
        options["precedence"] = data["precedence"]
    return service.validate_target_column_conflicts(
        rule_id, _payload_records(data), **options)


@router.post("/import_batch/{batch_id}/settle", response_model=dict)
def settle_batch(batch_id: int, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """按字段映射校验明细结算批次：核实输入范围、实际入库数与拒绝数。

    请求体可传 ``records``、``rule_id``、``confirmed_keys`` 和
    ``target_table_confirmed``。目标表未确认时批次停在待确认，
    存在冲突未裁定行时批次进入待修订。
    """
    _require_entry(ImportBatchService.MODULE, batch_id)
    data = payload or {}
    options: dict[str, Any] = {}
    if data.get("rule_id") is not None:
        options["rule_id"] = data["rule_id"]
    if isinstance(data.get("confirmed_keys"), list):
        options["confirmed_keys"] = [str(key) for key in data["confirmed_keys"]]
    if data.get("target_table_confirmed") is not None:
        options["target_table_confirmed"] = bool(data["target_table_confirmed"])
    return ImportBatchService().settle_batch_with_rule(
        _payload_records(data), **options)


@router.post("/import_batch/{batch_id}/submit-revision", response_model=ActionResult)
def submit_revision(batch_id: int, payload: dict[str, Any] | None = None) -> ActionResult:
    """提交修订后的批次：仅在目标表确认后写入修订结果。

    已确认过的行只计入重复数，不再重复入库；仍有拒绝行时批次保持
    待修订，退回经办人继续核对字段映射校验明细。
    """
    _require_entry(ImportBatchService.MODULE, batch_id)
    data = payload or {}
    entry, settlement, message = ImportBatchService().submit_revised_batch(
        batch_id,
        _payload_records(data),
        rule_id=data.get("rule_id"),
        confirmed_keys=data.get("confirmed_keys") if isinstance(
            data.get("confirmed_keys"), list) else None,
        target_table_confirmed=bool(data.get("target_table_confirmed")),
    )
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=bool(settlement), message=message, entry=entry)
