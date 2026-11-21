import { request } from './client'
import type {
  BatchSettlement,
  FieldMappingValidation,
} from '@/data/types'

/** 生成字段映射校验明细：逐行核对写入同一目标列的源字段值。 */
export async function validateFieldMapping(
  ruleId: number,
  records: Record<string, string>[],
): Promise<FieldMappingValidation> {
  const response = await request(`/api/data_import_rule/${ruleId}/field-mapping-validation`, {
    method: 'POST',
    body: JSON.stringify({ records }),
  })
  if (!response.ok) throw new Error('字段映射校验失败')
  return await response.json()
}

/** 按字段映射校验明细结算批次：核实输入范围、实际入库与拒绝数。 */
export async function settleBatch(
  batchId: number,
  values: {
    records: Record<string, string>[]
    ruleId?: number
    confirmedKeys?: string[]
    targetTableConfirmed?: boolean
  },
): Promise<BatchSettlement> {
  const response = await request(`/api/import_batch/${batchId}/settle`, {
    method: 'POST',
    body: JSON.stringify({
      records: values.records,
      rule_id: values.ruleId,
      confirmed_keys: values.confirmedKeys,
      target_table_confirmed: values.targetTableConfirmed,
    }),
  })
  if (!response.ok) throw new Error('批次结算失败')
  return await response.json()
}

/** 提交修订后的批次：目标表确认后才会写回批次统计。 */
export async function submitRevision(
  batchId: number,
  values: {
    records: Record<string, string>[]
    ruleId?: number
    targetTableConfirmed: boolean
  },
): Promise<{ ok: boolean; message: string }> {
  const response = await request(`/api/import_batch/${batchId}/submit-revision`, {
    method: 'POST',
    body: JSON.stringify({
      records: values.records,
      rule_id: values.ruleId,
      target_table_confirmed: values.targetTableConfirmed,
    }),
  })
  return await response.json()
}
