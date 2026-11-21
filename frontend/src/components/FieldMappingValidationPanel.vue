<template>
  <section class="validation-panel" :data-testid="`conflict-panel-${entryType}`">
    <header class="validation-head">
      <div>
        <h3>字段映射校验明细</h3>
        <p class="page-desc">{{ hint }}</p>
      </div>
      <div class="page-actions">
        <button
          class="btn primary"
          type="button"
          data-testid="run-conflict-validation"
          :disabled="running"
          @click="runValidation"
        >
          {{ running ? '校验中…' : entryType === 'batch' ? '按校验明细结算批次' : '运行目标列冲突校验' }}
        </button>
        <button class="btn ghost" type="button" data-testid="reset-records" @click="resetRecords">
          恢复示例记录
        </button>
      </div>
    </header>

    <form class="analysis-form" @submit.prevent="addRecord">
      <label v-if="entryType === 'batch'">
        <span>入库规则ID</span>
        <input v-model="ruleIdText" data-testid="input-rule-id" placeholder="填写已生效入库规则ID" />
      </label>
      <label v-for="column in sourceColumns" :key="column">
        <span>{{ column }}</span>
        <input v-model="draft[column]" :data-testid="`input-${column}`" />
      </label>
      <button class="btn" type="submit" data-testid="add-record">加入待校验行</button>
    </form>

    <div class="stat-row">
      <article class="stat-card">
        <span class="stat-label">核实输入范围</span>
        <strong class="stat-value" data-testid="verified-scope">{{ summary.verified_scope }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">实际入库</span>
        <strong class="stat-value" data-testid="imported-count">{{ summary.imported }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">拒绝数</span>
        <strong class="stat-value" data-testid="rejected-count">{{ summary.rejected }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">重复数</span>
        <strong class="stat-value" data-testid="duplicated-count">{{ summary.duplicated }}</strong>
      </article>
    </div>

    <table class="data-table">
      <thead>
        <tr>
          <th>行号</th>
          <th>源记录</th>
          <th>目标列</th>
          <th>来源列原值</th>
          <th>判定</th>
          <th>采用来源列</th>
          <th>写入目标值</th>
          <th>处理</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="item in details"
          :key="`${item.行号}-${item.目标列}`"
          :data-testid="`detail-${item.行号}`"
          :data-status="item.状态"
        >
          <td>{{ item.行号 }}</td>
          <td>{{ item.源记录 }}</td>
          <td>{{ item.目标列 }}</td>
          <td>
            <span v-for="(value, column) in item.原值" :key="column" class="source-value">
              {{ column }}：{{ value === '' ? '（空）' : value }}
            </span>
          </td>
          <td :class="item.冲突 ? 'conflict-text' : ''">{{ item.状态 }}</td>
          <td>{{ item.采用来源列 ?? '—' }}</td>
          <td>{{ item.采用值 ?? '—' }}</td>
          <td>{{ item.处理 }}</td>
        </tr>
        <tr v-if="!details.length">
          <td colspan="8" class="empty-state">
            尚未生成字段映射校验明细，点击“{{ entryType === 'batch' ? '按校验明细结算批次' : '运行目标列冲突校验' }}”开始核对
          </td>
        </tr>
      </tbody>
    </table>

    <div v-if="entryType === 'batch'" class="revision-bar">
      <label class="checkbox-item">
        <input v-model="targetTableConfirmed" type="checkbox" data-testid="target-confirmed" />
        <span>目标表已确认</span>
      </label>
      <button
        class="btn"
        type="button"
        data-testid="submit-revision"
        :disabled="running || !targetTableConfirmed"
        @click="commitRevision"
      >
        提交修订后的批次
      </button>
    </div>

    <p v-if="errorMessage" class="error-text" data-testid="validation-error">{{ errorMessage }}</p>
    <p v-if="message" class="page-foot" data-testid="validation-message">{{ message }}</p>
  </section>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'

import { settleBatch, submitRevision, validateFieldMapping } from '@/api/validation-service'
import type { BatchSettlement, FieldMappingDetail, FieldMappingValidation } from '@/data/types'

const props = defineProps<{
  entryType: 'rule' | 'batch'
  entryId: number
  entryCode: string
}>()

// 示例来源列：旧工商号与统一信用代码都会写入工商标识。
const sourceColumns = ['旧工商号', '统一信用代码']
const sampleRecords = [
  { 旧工商号: '91310000MA1K35Q32L', 统一信用代码: '91310000MA1K35Q32L' },
  { 旧工商号: '3100001234567', 统一信用代码: '91310000MA1K35Q32X' },
  { 旧工商号: '3100007654321', 统一信用代码: '' },
]
const records = ref<Record<string, string>[]>(sampleRecords.map((item) => ({ ...item })))
const draft = ref<Record<string, string>>({ 旧工商号: '', 统一信用代码: '' })
const ruleIdText = ref('')
const details = ref<FieldMappingDetail[]>([])
const summary = ref({ verified_scope: 0, imported: 0, rejected: 0, duplicated: 0 })
const running = ref(false)
const errorMessage = ref('')
const message = ref('')
const targetTableConfirmed = ref(false)

const hint = computed(() =>
  props.entryType === 'batch'
    ? `批次结算以字段映射校验明细为准：每条原始行只计一次，实际入库与拒绝数据此计算；目标表确认后才能提交修订。`
    : `同一目标列由旧工商号与统一信用代码共同写入，运行校验可看到两边原值是否一致。`,
)

function resetRecords() {
  records.value = sampleRecords.map((item) => ({ ...item }))
  details.value = []
  message.value = ''
  errorMessage.value = ''
}

function addRecord() {
  if (!draft.value.旧工商号?.trim() && !draft.value.统一信用代码?.trim()) {
    errorMessage.value = '请至少填写一个来源列的值'
    return
  }
  records.value = [...records.value, { ...draft.value }]
  draft.value = { 旧工商号: '', 统一信用代码: '' }
  errorMessage.value = ''
}

function resolveRuleId(): number {
  if (props.entryType === 'rule') return Number(props.entryId)
  const parsed = Number(ruleIdText.value)
  if (!Number.isFinite(parsed) || parsed <= 0) {
    throw new Error('请填写已生效的入库规则ID后再结算批次')
  }
  return parsed
}

function applyValidation(result: FieldMappingValidation) {
  details.value = result.detail
  summary.value = {
    verified_scope: result.verified_scope,
    imported: result.importable,
    rejected: result.rejected,
    duplicated: 0,
  }
  message.value = `已核实 ${result.verified_scope} 条原始行，${result.rejected} 行因目标列冲突退回修订`
}

function applySettlement(result: BatchSettlement) {
  details.value = result.detail
  summary.value = {
    verified_scope: result.verified_scope,
    imported: result.imported,
    rejected: result.rejected,
    duplicated: result.duplicated,
  }
  message.value = `核实输入范围 ${result.verified_scope} 行；实际入库 ${result.imported} 行，拒绝 ${result.rejected} 行，重复 ${result.duplicated} 行；批次状态：${result.batch_status}`
}

async function runValidation() {
  running.value = true
  errorMessage.value = ''
  message.value = ''
  try {
    const ruleId = resolveRuleId()
    if (props.entryType === 'rule') {
      const result = await validateFieldMapping(ruleId, records.value)
      if (!result.ok) throw new Error(result.reason ?? '字段映射校验失败')
      applyValidation(result)
    } else {
      const result = await settleBatch(Number(props.entryId), {
        records: records.value,
        ruleId,
        targetTableConfirmed: targetTableConfirmed.value,
      })
      if (!result.ok) throw new Error(result.reason ?? '批次结算失败')
      applySettlement(result)
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '校验失败'
  } finally {
    running.value = false
  }
}

async function commitRevision() {
  running.value = true
  errorMessage.value = ''
  try {
    const ruleId = resolveRuleId()
    if (!targetTableConfirmed.value) throw new Error('目标表尚未确认，暂不能提交修订后的批次')
    const result = await submitRevision(Number(props.entryId), {
      records: records.value,
      ruleId,
      targetTableConfirmed: targetTableConfirmed.value,
    })
    if (!result.ok) throw new Error(result.message)
    message.value = result.message
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '提交修订失败'
  } finally {
    running.value = false
  }
}
</script>

<style scoped>
.validation-panel {
  margin-top: 16px;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px 14px;
}
.validation-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
}
.validation-head h3 {
  margin: 0;
  font-size: 15px;
}
.source-value {
  display: block;
  font-size: 12px;
}
.conflict-text {
  color: #b42318;
  font-weight: 600;
}
.revision-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 12px;
}
.checkbox-item {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
}
</style>
