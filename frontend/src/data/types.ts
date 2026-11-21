/** 纯前端数据层的公共类型：与全栈版后端返回的结构保持一致，换回后端时页面不用改。 */

export type EntryRow = {
  id: number
  status: string
  pending: boolean
  abnormal: boolean
  [field: string]: string | number | boolean
}

export type ModuleMeta = {
  key: string
  name: string
  entity: string
  desc: string
  fields: string[]
  statuses: string[]
  actions: string[]
  actionTargets: Record<string, string>
  metrics: string[]
}

export type PageResult = {
  items: EntryRow[]
  total: number
  page: number
  size: number
}

export type ActionResult = {
  ok: boolean
  message: string
}

export type OverviewResult = {
  cards: { label: string; value: number }[]
  modules: { name: string; created: number; pending: number; abnormal: number }[]
}

/** 字段映射校验明细：一条源记录在单个目标列上的判定结果。 */
export type FieldMappingDetail = {
  行号: number
  源记录: string
  目标列: string
  来源列: string[]
  原值: Record<string, string>
  状态: string
  冲突: boolean
  采用值: string | null
  采用来源列: string | null
  进入目标列: boolean
  处理: string
  依据: string
}

export type FieldMappingRow = {
  行号: number
  源记录: string
  状态: string
  冲突: boolean
  可入库: boolean
  映射结果: Record<string, string>
  目标列明细: FieldMappingDetail[]
}

export type FieldMappingValidation = {
  ok: boolean
  reason?: string
  规则编号?: string
  total: number
  verified_scope: number
  target_columns: string[]
  detail: FieldMappingDetail[]
  row_results: FieldMappingRow[]
  importable: number
  rejected: number
}

export type BatchSettlement = {
  ok: boolean
  reason?: string
  total: number
  verified_scope: number
  imported: number
  rejected: number
  duplicated: number
  counted_once: boolean
  batch_status: string
  target_table_confirmed?: boolean
  detail: FieldMappingDetail[]
  row_results: FieldMappingRow[]
}

