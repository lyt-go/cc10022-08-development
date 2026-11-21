"""目标列冲突校验测试：字段映射校验明细、批次结算口径与已确认行去重。"""
from __future__ import annotations

import os
import sys

_backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from app.services.data_import_rule import DataImportRuleService
from app.services.import_batch import ImportBatchService
from app.store import store


# 同一目标列「工商标识」由两个来源列合并映射而来。
MAPPING_RULE = {
    "id": 901,
    "规则编号": "DATA-901",
    "来源系统": "CRM",
    "实体模型": "customer_master",
    "字段映射": "旧工商号->工商标识,统一信用代码->工商标识",
    "校验规则": "统一信用代码>旧工商号",
    "出错策略": "冲突则整行拒绝",
    "分片大小": "1000",
    "规则版本": "v1",
    "status": "已生效",
}

RECORDS = [
    # 两列值一致（仅格式差异）。
    {"id": 1, "旧工商号": "91310000-MA1K35Q32L", "统一信用代码": "91310000MA1K35Q32L"},
    # 两列值不一致，规则里有来源优先级，可按规则裁定。
    {"id": 2, "旧工商号": "3100001234567", "统一信用代码": "91310000MA1K35Q32X"},
    # 只有一列有值。
    {"id": 3, "旧工商号": "3100007654321", "统一信用代码": ""},
]


class TestTargetColumnConflict:
    def setup_method(self):
        # 每个用例使用独立的目标实体，并清掉本测试写入的批次记录，
        # 避免上一个用例的已确认标识干扰去重口径。
        store._tables.pop("customer_master", None)
        batches = store.rows("import_batch")
        batches[:] = [row for row in batches if row.get("规则编号") != MAPPING_RULE["规则编号"]]

    # ── 明细：两边值一致 / 单列有值 / 冲突 ──
    def test_detail_marks_consistent_single_and_conflict(self):
        svc = DataImportRuleService()
        result = svc.validate_target_column_conflicts(MAPPING_RULE, RECORDS)

        assert result["ok"] is True
        assert result["verified_scope"] == 3
        statuses = [row["状态"] for row in result["row_results"]]
        assert statuses == ["通过", "按规则裁定", "通过"]

        first, second, third = result["detail"]
        # 值一致：目标列只写一个值，明细仍能看到两边原值。
        assert first["状态"] == "一致"
        assert first["原值"] == {
            "旧工商号": "91310000-MA1K35Q32L",
            "统一信用代码": "91310000MA1K35Q32L",
        }
        assert first["进入目标列"] is True
        # 单列有值：空值不参与冲突判断。
        assert third["状态"] == "单列有值"
        assert third["采用来源列"] == "旧工商号"
        # 冲突行原样保留原值和来源列。
        assert second["原值"]["旧工商号"] == "3100001234567"
        assert second["原值"]["统一信用代码"] == "91310000MA1K35Q32X"
        assert second["来源列"] == ["旧工商号", "统一信用代码"]

    def test_conflict_without_basis_keeps_target_empty(self):
        svc = DataImportRuleService()
        rule = {**MAPPING_RULE, "校验规则": "字段长度不超过18位"}
        result = svc.validate_target_column_conflicts(rule, RECORDS)

        conflict_rows = [row for row in result["row_results"] if row["冲突"]]
        assert [row["行号"] for row in conflict_rows] == [2]
        assert conflict_rows[0]["mapped"] == {}, "无裁定依据时不能把任一值写入目标列"
        assert conflict_rows[0]["importable"] is False

        conflict_detail = next(d for d in result["detail"] if d["冲突"])
        assert conflict_detail["采用值"] is None
        assert conflict_detail["采用来源列"] is None
        assert conflict_detail["原值"]["旧工商号"] == "3100001234567"
        assert conflict_detail["原值"]["统一信用代码"] == "91310000MA1K35Q32X"

        # 其余行不受影响。
        assert [row["importable"] for row in result["row_results"]] == [True, False, True]

    def test_rule_based_resolution_uses_declared_source(self):
        svc = DataImportRuleService()
        rule = {**MAPPING_RULE, "校验规则": "以统一信用代码为准"}
        result = svc.validate_target_column_conflicts(rule, RECORDS)
        second = result["row_results"][1]
        assert second["状态"] == "按规则裁定"
        assert second["mapped"] == {"工商标识": "91310000MA1K35Q32X"}
        assert second["importable"] is True

    def test_explicit_precedence_overrides_rule_text(self):
        svc = DataImportRuleService()
        rule = {**MAPPING_RULE, "校验规则": ""}
        result = svc.validate_target_column_conflicts(
            rule, RECORDS, precedence={"工商标识": ["旧工商号", "统一信用代码"]})
        assert result["row_results"][1]["mapped"] == {"工商标识": "3100001234567"}

    # ── 批次结算：每条原始行只计一次 ──
    def test_batch_settlement_counts_each_raw_row_once(self):
        batch_svc = ImportBatchService()
        settlement = batch_svc.settle_batch_with_rule(RECORDS, rule_id=MAPPING_RULE)

        assert settlement["verified_scope"] == len(RECORDS)
        assert settlement["total"] == len(RECORDS)
        assert (
            settlement["imported"] + settlement["rejected"] + settlement["duplicated"]
            == len(RECORDS)
        ), "每条原始行只能计入成功、拒绝或重复之一"
        assert settlement["counted_once"] is True
        assert len(settlement["row_results"]) == len(RECORDS)
        assert len({row["行号"] for row in settlement["row_results"]}) == len(RECORDS)

    def test_batch_rejected_rows_and_revision_status(self):
        batch_svc = ImportBatchService()
        rule = {**MAPPING_RULE, "校验规则": "字段长度不超过18位"}
        settlement = batch_svc.settle_batch_with_rule(RECORDS, rule_id=rule)

        assert settlement["verified_scope"] == 3
        assert settlement["imported"] == 2
        assert settlement["rejected"] == 1
        assert settlement["batch_status"] == "待修订"
        assert settlement["requires_revision"] is True
        assert settlement["conflicts"][0]["行号"] == 2

    def test_unconfirmed_target_table_holds_batch(self):
        batch_svc = ImportBatchService()
        settlement = batch_svc.settle_batch_with_rule(
            RECORDS, rule_id=MAPPING_RULE, target_table_confirmed=False)
        assert settlement["batch_status"] == "待确认"
        assert settlement["requires_confirmation"] is True

        entry = batch_svc.import_batch_from_source(
            "CRM", "customer_master", RECORDS, rule_id=MAPPING_RULE)
        assert entry["批次状态"] == "已完成"
        assert entry["verified_scope"] == 3

    def test_confirmed_rows_are_not_imported_twice(self):
        batch_svc = ImportBatchService()
        first = batch_svc.import_batch_from_source(
            "CRM", "customer_master", RECORDS, rule_id=MAPPING_RULE)
        assert first["成功数"] == "3"
        assert first["重复数"] == "0"
        assert len(first["field_mapping_details"]) == 3

        # 同一批记录再次提交：三条都已确认，只计重复，不再入库。
        second = batch_svc.import_batch_from_source(
            "CRM", "customer_master", RECORDS, rule_id=MAPPING_RULE)
        assert second["总记录数"] == "3"
        assert second["成功数"] == "0"
        assert second["重复数"] == "3"
        assert second["失败数"] == "0"
        assert second["verified_scope"] == 3

    def test_confirmed_target_customer_record_skips_reimport(self):
        # 目标实体里已确认的客户记录：再次导入同一工商标识只计重复。
        store.rows("customer_master").append({
            "id": 1, "工商标识": "91310000MA1K35Q32L",
            "status": "已生效", "pending": False, "abnormal": False,
        })
        settlement = ImportBatchService().settle_batch_with_rule(
            RECORDS, rule_id=MAPPING_RULE)
        assert settlement["duplicated"] == 1
        assert settlement["imported"] == 2
        assert settlement["rejected"] == 0
        assert settlement["counted_once"] is True

    def test_submit_revised_batch_requires_confirmation(self):
        batch_svc = ImportBatchService()
        entry = batch_svc.import_batch_from_source(
            "CRM", "customer_master", [{"id": 9, "旧工商号": "X1"}],
            rule_id=MAPPING_RULE)
        batch_id = entry["id"]

        blocked, settlement, message = batch_svc.submit_revised_batch(
            batch_id, RECORDS, rule_id=MAPPING_RULE, target_table_confirmed=False)
        assert settlement == {}
        assert "尚未确认" in message
        assert blocked["成功数"] == "1", "目标表未确认时不能改写批次统计"

        # 目标表确认后提交修订：三条记录均未入库过，全部计实际入库。
        revised, settlement, message = batch_svc.submit_revised_batch(
            batch_id, RECORDS, rule_id=MAPPING_RULE, target_table_confirmed=True)
        assert settlement["verified_scope"] == 3
        assert settlement["imported"] == 3
        assert settlement["duplicated"] == 0
        assert revised["成功数"] == "3"
        assert revised["重复数"] == "0"
        assert revised["verified_scope"] == 3
        assert len(revised["field_mapping_details"]) == 3

        # 修订批次自身已结算的成功行同样计入确认范围，再次提交只计重复。
        again, settlement, _ = batch_svc.submit_revised_batch(
            batch_id, RECORDS, rule_id=MAPPING_RULE, target_table_confirmed=True)
        assert settlement["imported"] == 0
        assert settlement["duplicated"] == 3
        assert again["成功数"] == "0"


if __name__ == "__main__":
    import traceback

    runner = TestTargetColumnConflict()
    tests = [
        ("明细区分一致/单列有值/冲突", runner.test_detail_marks_consistent_single_and_conflict),
        ("无依据时目标列留空", runner.test_conflict_without_basis_keeps_target_empty),
        ("按规则优先级裁定", runner.test_rule_based_resolution_uses_declared_source),
        ("显式优先级覆盖规则文本", runner.test_explicit_precedence_overrides_rule_text),
        ("批次每行只计一次", runner.test_batch_settlement_counts_each_raw_row_once),
        ("拒绝行触发待修订", runner.test_batch_rejected_rows_and_revision_status),
        ("目标表未确认时挂起批次", runner.test_unconfirmed_target_table_holds_batch),
        ("已确认行不重复入库", runner.test_confirmed_rows_are_not_imported_twice),
        ("目标客户记录已确认时计重复", runner.test_confirmed_target_customer_record_skips_reimport),
        ("提交修订批次需先确认目标表", runner.test_submit_revised_batch_requires_confirmation),
    ]
    passed = failed = 0
    for name, test_fn in tests:
        try:
            runner.setup_method()
            test_fn()
            print(f"  PASS: {name}")
            passed += 1
        except Exception:
            print(f"  FAIL: {name}")
            traceback.print_exc()
            failed += 1
    print(f"\n结果: {passed}/{len(tests)} 通过, {failed} 失败")
    assert failed == 0
