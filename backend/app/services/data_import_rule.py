"""入库规则领域服务：字段映射校验、数据转换编排与导入规则管理。"""
from __future__ import annotations

import json
import re
from typing import Any, Iterable

from app.services.base import BaseModuleService
from app.store import store

_MAPPING_CHUNK_SEPARATOR = re.compile(r"[,，;；\n]")
_MAPPING_ARROW = re.compile(r"->|→|=>")
_SOURCE_SEPARATOR = re.compile(r"[+|]")
_VALUE_SEPARATOR = re.compile(r"[\s\-_/]+")


class DataImportRuleService(BaseModuleService):
    MODULE = "data_import_rule"
    ENTITY = "入库规则"
    OPERATION_TERM = "导入"
    LIST_FIELDS = ["规则编号", "来源系统", "实体模型", "字段映射", "校验规则", "出错策略", "分片大小", "规则版本"]
    REQUIRED_FIELDS = ["规则编号", "来源系统", "实体模型"]
    STATUS_ORDER = ["草稿", "待校验", "已生效", "已停用"]
    ACTION_RULES = {"提交校验": "待校验", "标记生效": "已生效", "停用规则": "已停用"}
    NEGATIVE_ACTIONS = ["停用规则"]

    # 目标列校验状态：一条源记录在同一目标列上可能遇到的全部判定结果。
    STATUS_CONSISTENT = "一致"
    STATUS_SINGLE = "单列有值"
    STATUS_EMPTY = "无值"
    STATUS_CONFLICT = "冲突"
    STATUS_RESOLVED = "按规则裁定"
    CONFLICT_STATUSES = (STATUS_CONFLICT,)

    # ── 字段映射解析 ────────────────────────────────────────────────

    def parse_field_mapping(self, rule_or_spec: dict[str, Any] | str) -> list[tuple[str, str]]:
        """解析字段映射配置，返回 (源字段, 目标列) 的有序列表。

        支持 ``源字段->目标列``、``源字段→目标列``，多个映射之间用中英文
        逗号、分号或换行分隔。也支持 ``旧工商号+统一信用代码->工商标识``
        以及 ``旧工商号,统一信用代码->工商标识`` 这类把多个来源列并入
        同一目标列的写法；同一目标列出现多次时保留全部来源供冲突校验
        使用，完全重复的映射对会被去重。
        """
        if isinstance(rule_or_spec, dict):
            spec: Any = rule_or_spec.get("字段映射", "")
        else:
            spec = rule_or_spec
        if isinstance(spec, dict):
            return [(str(source).strip(), str(target).strip())
                    for source, target in spec.items() if str(target).strip()]
        text = str(spec or "")
        if text.strip().startswith("{"):
            try:
                decoded = json.loads(text)
            except (TypeError, ValueError):
                decoded = None
            if isinstance(decoded, dict):
                return [(str(source).strip(), str(target).strip())
                        for source, target in decoded.items() if str(target).strip()]

        pairs: list[tuple[str, str]] = []
        pending: list[str] = []
        for chunk in _MAPPING_CHUNK_SEPARATOR.split(text):
            chunk = chunk.strip()
            if not chunk:
                continue
            parts = _MAPPING_ARROW.split(chunk, maxsplit=1)
            if len(parts) != 2:
                # 没有箭头的片段可能是下一段共享的目标来源，如
                # ``旧工商号,统一信用代码->工商标识`` 中的前半段。
                pending.extend(
                    source.strip() for source in _SOURCE_SEPARATOR.split(chunk)
                    if source.strip())
                continue
            target = parts[1].strip()
            if not target:
                pending = []
                continue
            sources = [
                *(source for source in pending),
                *(_SOURCE_SEPARATOR.split(parts[0])),
            ]
            pending = []
            for source in sources:
                source = source.strip()
                if source and (source, target) not in pairs:
                    pairs.append((source, target))
        return pairs

    # 兼容旧调用方的私有命名。
    _parse_field_mapping = parse_field_mapping

    @staticmethod
    def _target_groups(pairs: Iterable[tuple[str, str]]) -> dict[str, list[str]]:
        """按目标列归集来源字段，保持映射配置中的先后顺序。"""
        groups: dict[str, list[str]] = {}
        for source, target in pairs:
            groups.setdefault(target, []).append(source)
        return groups

    # ── 目标列冲突校验 ──────────────────────────────────────────────

    @staticmethod
    def _normalize_value(value: Any) -> str:
        """把标识类字段归一化后比较：忽略空白、常见分隔符和大小写。"""
        if value is None:
            return ""
        text = str(value).strip()
        if not text:
            return ""
        return _VALUE_SEPARATOR.sub("", text).upper()

    @classmethod
    def _is_blank(cls, value: Any) -> bool:
        return cls._normalize_value(value) == ""

    @staticmethod
    def _ordered_priority(text: str, source_columns: list[str]) -> list[str]:
        """从校验规则文本中提取来源列优先级。

        支持 ``统一信用代码>旧工商号``、``统一信用代码优先`` 以及
        ``以统一信用代码为准`` 三种写法；没有优先级语义时返回空列表，
        表示缺少裁定依据。返回顺序与文本中出现的先后一致。
        """
        if not text:
            return []
        scope = text
        if "为准" in text:
            # “以…为准”：只有“为准”之前提到的来源列参与裁定。
            scope = text.split("为准", 1)[0]
        positions = {
            source: scope.index(source)
            for source in source_columns
            if source in scope
        }
        if not positions:
            return []
        has_marker = any(marker in text for marker in (">", "优先", "为准"))
        if not has_marker:
            return []
        return [
            source for source, _ in sorted(positions.items(), key=lambda item: item[1])
        ]

    def _target_priority(
        self,
        rule: dict[str, Any],
        target: str,
        source_columns: list[str],
        precedence: dict[str, list[str]] | list[str] | None = None,
    ) -> list[str]:
        """确定指定目标列的来源优先级；没有配置时返回空列表。"""
        if precedence:
            explicit: list[str] | None
            if isinstance(precedence, dict):
                explicit = (
                    precedence.get(target)
                    or precedence.get("default")
                    or precedence.get("*")
                )
            else:
                explicit = list(precedence)
            if explicit:
                ordered = [source for source in explicit if source in source_columns]
                if ordered:
                    return ordered

        # 校验规则优先，其次看目标列冲突策略和冲突出错策略；只有带
        # 优先级语义的文本才作为裁定依据。
        for field in ("校验规则", "冲突策略", "目标列冲突策略", "出错策略"):
            ordered = self._ordered_priority(str(rule.get(field, "") or ""), source_columns)
            if ordered:
                return ordered
        return []

    def _build_target_detail(
        self,
        row_no: int,
        source_record: str,
        target: str,
        source_columns: list[str],
        record: dict[str, Any],
        basis: list[str],
    ) -> dict[str, Any]:
        """生成一条源记录在单个目标列上的字段映射校验明细。"""
        present = [
            (source, record.get(source))
            for source in source_columns
            if source in record and not self._is_blank(record.get(source))
        ]
        original_values = {source: record.get(source) for source in source_columns if source in record}
        normalized = {source: self._normalize_value(value) for source, value in present}
        distinct = {value for value in normalized.values() if value}

        # 来源列在规则中的优先级：候选列全部列全，便于明细直接展示依据。
        priority_order = [
            *(source for source in basis if source in source_columns),
            *(source for source in source_columns if source not in basis),
        ]

        resolved_value: Any = None
        resolved_source: str | None = None
        entered = False
        basis_note = ""
        if not present:
            status = self.STATUS_EMPTY
            action = "目标列留空"
        elif len(distinct) == 1:
            status = self.STATUS_CONSISTENT if len(present) > 1 else self.STATUS_SINGLE
            chosen = next(
                (source for source in basis if source in normalized),
                present[0][0],
            )
            resolved_source = chosen
            resolved_value = next(
                value for source, value in present if source == chosen
            )
            entered = True
            action = "入库"
            basis_note = "多列取值一致" if status == self.STATUS_CONSISTENT else "仅一个来源列有值"
        else:
            # 两个及以上非空值不一致：先按显式优先级裁定；没有依据时
            # 目标列必须留空，源记录退回修订，不能把任一值写入目标列。
            chosen = next((source for source in basis if source in normalized), None)
            if chosen is None:
                status = self.STATUS_CONFLICT
                action = "退回修订"
                basis_note = "缺少裁定依据，目标列留空"
            else:
                status = self.STATUS_RESOLVED
                resolved_source = chosen
                resolved_value = next(
                    value for source, value in present if source == chosen
                )
                entered = True
                action = "入库"
                basis_note = f"按来源优先级采用「{chosen}」"

        return {
            "行号": row_no,
            "row_index": row_no,
            "源记录": source_record,
            "source_record": source_record,
            "目标列": target,
            "target_column": target,
            "来源列": list(source_columns),
            "source_columns": list(source_columns),
            "来源优先级": priority_order,
            "原值": original_values,
            "original_values": original_values,
            "非空值": {source: value for source, value in present},
            "冲突": status == self.STATUS_CONFLICT,
            "status": status,
            "状态": status,
            "采用值": resolved_value,
            "resolved_value": resolved_value,
            "采用来源列": resolved_source,
            "resolved_source": resolved_source,
            "进入目标列": entered,
            "entered_target": entered,
            "处理": action,
            "action": action,
            "依据": basis_note,
        }

    def validate_target_column_conflicts(
        self,
        rule_id: int | dict[str, Any],
        records: list[dict[str, Any]] | None,
        *,
        target_column: str | None = None,
        precedence: dict[str, list[str]] | list[str] | None = None,
        include_single_source: bool = False,
    ) -> dict[str, Any]:
        """目标列冲突校验：检查多个源字段写入同一目标列时的值一致性。

        一条源记录的两个（或更多）源字段映射到同一目标列时：

        * 非空值一致：目标列写入该值，明细标记为“一致”；
        * 只有一个源字段有值：目标列写入该值，明细标记为“单列有值”；
        * 两个非空值不一致：明细记录原值和来源列，按校验规则中的来源
          优先级裁定；没有裁定依据时目标列留空，该行退回修订；
        * 全部为空：目标列留空。

        返回结果以原始输入行为结算单位，``row_results`` 中每行只出现
        一次，``verified_scope`` 即本次核实过的输入行数。
        """
        rule = self._resolve_rule(rule_id)
        if rule is None:
            return {
                "ok": False,
                "reason": "入库规则不存在",
                "total": len(records or []),
                "verified_scope": 0,
                "detail": [],
                "details": [],
                "row_results": [],
            }

        pairs = self.parse_field_mapping(rule)
        groups = self._target_groups(pairs)
        all_records = list(records or [])

        requested_target = target_column or rule.get("目标列") or rule.get("冲突校验目标列")
        if requested_target:
            target_columns = [
                str(target) for target in
                (requested_target if isinstance(requested_target, (list, tuple))
                 else [requested_target])
            ]
        else:
            # 默认核对所有“多个来源列写入同一目标列”的目标列，这样明细
            # 正好覆盖需要判断值一致性的列；若规则没有合并映射，则退回
            # 全部目标列，保证普通单来源映射也能生成明细。
            target_columns = [
                target for target, sources in groups.items()
                if include_single_source or len(sources) > 1
            ] or list(groups)

        detail: list[dict[str, Any]] = []
        row_results: list[dict[str, Any]] = []
        for index, record in enumerate(all_records, start=1):
            source_record = str(
                record.get("源记录编号")
                or record.get("来源记录")
                or record.get("记录编号")
                or record.get("id")
                or index
            )
            mapped: dict[str, Any] = {}
            row_detail: list[dict[str, Any]] = []
            row_conflicts: list[dict[str, Any]] = []
            row_resolved: list[dict[str, Any]] = []
            row_missing: list[dict[str, Any]] = []

            for target in target_columns:
                sources = groups.get(target, [])
                if not sources:
                    continue
                basis = self._target_priority(rule, target, sources, precedence)
                entry = self._build_target_detail(
                    index, source_record, target, sources, record, basis)
                row_detail.append(entry)
                detail.append(entry)
                if entry["状态"] == self.STATUS_CONFLICT:
                    row_conflicts.append(entry)
                elif entry["状态"] == self.STATUS_RESOLVED:
                    row_resolved.append(entry)
                elif entry["状态"] == self.STATUS_EMPTY:
                    row_missing.append(entry)
                if entry["进入目标列"]:
                    # 同一目标列只会写入一个裁定后的值。
                    mapped[target] = entry["采用值"]

            if not pairs:
                # 未配置字段映射时保持原有直通语义，但同样按原始行结算。
                mapped = {
                    key: value for key, value in record.items()
                    if key not in ("id", "status", "pending", "abnormal")
                }
                row_status = "未配置映射"
                importable = True
            elif row_conflicts:
                row_status = self.STATUS_CONFLICT
                importable = False
            elif row_resolved:
                row_status = self.STATUS_RESOLVED
                importable = True
            else:
                row_status = "通过"
                importable = True

            # 标识值只取真正写入目标列的值：冲突落选的原值不能作为
            # 已入库标识，否则会误拦后续以该值为准的客户记录。
            identifiers = {
                self._normalize_value(value)
                for value in mapped.values()
                if self._normalize_value(value)
            }
            original_values: dict[str, Any] = {}
            for entry in row_detail:
                original_values.update(entry["原值"])

            row_result = {
                "行号": index,
                "row_index": index,
                "源记录": source_record,
                "source_record": source_record,
                "状态": row_status,
                "status": row_status,
                "冲突": bool(row_conflicts),
                "可入库": importable,
                "importable": importable,
                "可入库数": 1 if importable else 0,
                "映射结果": mapped,
                "mapped": mapped,
                "目标列明细": row_detail,
                "detail": row_detail,
                "原值": original_values,
                "original_values": original_values,
                "标识值": sorted(value for value in identifiers if value),
                "identifiers": sorted(value for value in identifiers if value),
                "来源列": sorted({source for entry in row_detail for source in entry["来源列"]}),
                "退回修订": not importable,
                "requires_revision": not importable,
            }
            row_results.append(row_result)

        importable_rows = [row for row in row_results if row["importable"]]
        rejected_rows = [row for row in row_results if not row["importable"]]
        counts_by_status: dict[str, int] = {}
        for row in row_results:
            counts_by_status[row["状态"]] = counts_by_status.get(row["状态"], 0) + 1

        return {
            "ok": True,
            "rule_id": rule.get("id"),
            "规则编号": rule.get("规则编号"),
            "entity_model": rule.get("实体模型"),
            "实体模型": rule.get("实体模型"),
            "target_columns": target_columns,
            "目标列": target_columns,
            "total": len(all_records),
            "verified_scope": len(all_records),
            "核实输入范围": len(all_records),
            "verified_row_numbers": [row["行号"] for row in row_results],
            "detail": detail,
            "details": detail,
            "字段映射校验明细": detail,
            "row_results": row_results,
            "rows": row_results,
            "conflicts": [entry for entry in detail if entry["冲突"]],
            "resolved_conflicts": [entry for entry in detail if entry["状态"] == self.STATUS_RESOLVED],
            "has_conflict": any(row["冲突"] for row in row_results),
            "importable": len(importable_rows),
            "rejected": len(rejected_rows),
            "importable_rows": importable_rows,
            "rejected_rows": rejected_rows,
            "counts_by_status": counts_by_status,
        }

    # 供不同调用场景使用的语义化别名。
    def build_field_mapping_validation_detail(
        self,
        rule_id: int | dict[str, Any],
        records: list[dict[str, Any]] | None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """生成字段映射校验明细（目标列冲突校验的明细视图）。"""
        return self.validate_target_column_conflicts(rule_id, records, **kwargs)

    def generate_field_mapping_validation_detail(
        self,
        rule_id: int | dict[str, Any],
        records: list[dict[str, Any]] | None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """生成字段映射校验明细，供批次导入管理认定核实输入范围。"""
        return self.validate_target_column_conflicts(rule_id, records, **kwargs)

    def validate_field_mapping(
        self,
        rule_id: int | dict[str, Any],
        records: list[dict[str, Any]] | None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """字段映射校验入口，返回逐行结果和目标列冲突明细。"""
        return self.validate_target_column_conflicts(rule_id, records, **kwargs)

    def validate_field_mapping_conflicts(
        self,
        rule_id: int | dict[str, Any],
        records: list[dict[str, Any]] | None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """目标列冲突校验（语义化别名）。"""
        return self.validate_target_column_conflicts(rule_id, records, **kwargs)

    def apply_field_mapping_with_validation(
        self,
        rule_id: int | dict[str, Any],
        records: list[dict[str, Any]] | None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """执行字段映射并附带目标列冲突校验结果。"""
        return self.validate_target_column_conflicts(rule_id, records, **kwargs)

    def _resolve_rule(self, rule_id: int | dict[str, Any]) -> dict[str, Any] | None:
        if isinstance(rule_id, dict):
            return rule_id
        return self.get_entry(int(rule_id))

    # ── 规则管理与导入编排 ──────────────────────────────────────────

    def import_data_with_rules(self, rule_id: int, raw_data: list[dict[str, Any]]) -> dict[str, Any]:
        """导入数据批次的字段映射与校验规则编排。

        根据入库规则执行字段映射转换，校验每条记录的必填字段完整性，
        同时产出目标列冲突校验明细；存在未裁定冲突的行退回修订，
        不写入目标字段。统计量按原始输入行计，每行只计一次。
        """
        rule = self.get_entry(rule_id)
        if not rule or rule.get("status") != "已生效":
            return {"ok": False, "reason": "入库规则不可用或未生效"}
        validation = self.validate_target_column_conflicts(rule_id, raw_data)
        target_module = str(rule.get("实体模型", "entity_model") or "entity_model")

        mapped = failed = duped = 0
        for row in validation["row_results"]:
            mapped_record = row["mapped"]
            if not row["importable"] or not mapped_record:
                failed += 1
                continue
            # 校验目标实体中是否已存在同一记录，避免重复入库。
            if self._record_exists(target_module, mapped_record):
                duped += 1
                continue
            mapped += 1
        return {
            "total": len(raw_data),
            "imported": mapped,
            "failed": failed,
            "duplicated": duped,
            "verified_scope": validation["verified_scope"],
            "rejected": failed,
            "field_mapping_details": validation["detail"],
            "details": validation["detail"],
            "row_results": validation["row_results"],
        }

    def _apply_field_mapping(self, record: dict[str, Any], rule: dict[str, Any]) -> dict[str, Any] | None:
        """解析字段映射规则，执行源字段到目标字段的转换和标准化。

        同一目标列存在多个来源字段时，只有值一致或能按规则裁定时才
        写入目标列；两个非空值冲突且无依据时目标列留空。
        """
        pairs = self.parse_field_mapping(rule)
        if not pairs:
            return record
        validation = self.validate_target_column_conflicts(rule, [record])
        if not validation["ok"] or not validation["row_results"]:
            return None
        row = validation["row_results"][0]
        return row["mapped"] or None

    def _record_exists(self, module: str, record: dict[str, Any]) -> bool:
        """校验目标实体中是否已存在相同记录，用于去重判断。"""
        rows = store.rows(module)
        for row in rows:
            shared = [k for k in record if k in row]
            # 没有共同字段时不能判定为同一条记录，否则空表以外的模块
            # 会被整体误判成重复。
            if shared and all(record.get(k) == row.get(k) for k in shared):
                return True
        return False

    def validate_import_rules(self, source_system: str) -> list[dict[str, Any]]:
        """校验指定来源系统的所有入库规则是否完整可用。

        查询来源系统关联的入库规则，校验字段映射、校验规则、
        出错策略和分片大小的完整性，返回校验结果列表。
        """
        rows = store.rows(self.MODULE)
        relevant = [row for row in rows if row.get("来源系统") == source_system]
        results = []
        for rule in relevant:
            checks = {
                "rule_id": rule.get("规则编号"),
                "has_field_mapping": bool(rule.get("字段映射")),
                "has_validation": bool(rule.get("校验规则")),
                "has_error_strategy": bool(rule.get("出错策略")),
                "shard_size_valid": str(rule.get("分片大小", "")).isdigit(),
            }
            checks["all_valid"] = all([
                checks["has_field_mapping"], checks["has_validation"],
                checks["has_error_strategy"]
            ])
            results.append(checks)
        return results

    def query_import_rules_by_model(self, entity_model: str) -> list[dict[str, Any]]:
        """查询指定实体模型关联的所有已生效入库规则。

        遍历入库规则存储，筛选已生效且匹配实体模型的规则，
        按规则版本排序返回供批次导入编排使用。
        """
        rows = store.rows(self.MODULE)
        rules = [
            row for row in rows
            if row.get("实体模型") == entity_model and row.get("status") == "已生效"
        ]
        # 查询入库规则并按规则版本排序
        rules.sort(key=lambda r: r.get("规则版本", ""), reverse=True)
        return rules

    def import_batch_with_rule(self, rule_id: int, batch_data: list[dict[str, Any]],
                               *, confirmed_keys: Iterable[str] | None = None) -> dict[str, Any]:
        """导入批次数据：执行入库规则校验、字段标准化、去重检查。

        解析入库规则配置，对每条记录执行字段映射与数据转换，产出字段
        映射校验明细。冲突未裁定的行拒绝入库并退回修订；目标实体中
        已经确认的行不再重复入库。所有统计均以原始输入行为单位。
        """
        rule = self.get_entry(rule_id)
        if not rule:
            return {"ok": False, "reason": "入库规则不存在"}
        validation = self.validate_target_column_conflicts(rule_id, batch_data)
        target_module = str(rule.get("实体模型", "entity_model") or "entity_model")
        confirmed = {self._normalize_value(key) for key in (confirmed_keys or []) if not self._is_blank(key)}

        success = errors = duplicated = 0
        for row in validation["row_results"]:
            if not row["importable"] or not row["mapped"]:
                errors += 1
                continue
            if row["identifiers"] and any(
                identifier in confirmed for identifier in row["identifiers"]
            ):
                duplicated += 1
                continue
            if self._record_exists(target_module, row["mapped"]):
                duplicated += 1
                continue
            success += 1
        return {
            "batch_id": rule.get("规则编号"),
            "total": len(batch_data),
            "success": success,
            "errors": errors,
            "duplicated": duplicated,
            "imported": success,
            "rejected": errors,
            "verified_scope": validation["verified_scope"],
            "counted_once": success + errors + duplicated == len(batch_data),
            "field_mapping_details": validation["detail"],
            "details": validation["detail"],
            "row_results": validation["row_results"],
        }
