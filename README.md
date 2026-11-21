# 主数据清洗与黄金记录平台

面向多源主数据的匹配识别、冲突消解、合并清洗与黄金记录管理的主数据治理平台。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/WorkbenchView.vue|tsx  全部模块共享工作台
│   ├── src/data/modules.ts              18个业务模块配置
│   ├── src/api/              统一请求封装
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/resource.py  通用查询/导出/登记/动作接口
│   ├── app/services/<key>.py    每个模块独立领域逻辑
│   ├── app/services/base.py     共享服务骨架
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm ci
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 来源系统注册 | `source_system` | 来源系统 | 系统编号、系统名称、系统类型 |
| 实体模型定义 | `entity_model` | 实体模型 | 模型编号、模型名称、业务域 |
| 匹配规则引擎 | `match_rule` | 匹配规则 | 规则编号、实体模型、匹配字段 |
| 疑似重复簇 | `dup_cluster` | 重复簇 | 簇编号、实体模型、簇大小 |
| 合并追溯日志 | `merge_log` | 合并记录 | 记录编号、黄金ID、来源记录 |
| 黄金记录管理 | `golden_record` | 黄金记录 | 黄金ID、实体模型、关联来源 |
| 字段标准化清洗 | `field_cleanse` | 清洗规则 | 规则编号、实体模型、目标字段 |
| 优胜策略配置 | `survivorship_rule` | 优胜策略 | 策略编号、实体模型、选择字段 |
| 数据入库规则 | `data_import_rule` | 入库规则 | 规则编号、来源系统、实体模型 |
| 批次导入管理 | `import_batch` | 导入批次 | 批次编号、来源系统、实体模型 |
| 记录级血缘 | `record_lineage` | 血缘记录 | 血缘编号、黄金ID、来源记录 |
| 主数据分发同步 | `mdm_sync` | 同步配置 | 配置编号、黄金实体、目标系统 |
| 黄金记录版本 | `mdm_version` | 版本记录 | 版本编号、黄金ID、版本号 |
| 自动匹配任务 | `auto_match_job` | 匹配任务 | 任务编号、实体模型、匹配规则 |
| 主数据质量统计 | `mdm_quality` | 质量统计 | 统计编号、实体模型、完备率 |
| 参考数据管理 | `mdm_reference` | 参考数据 | 数据编号、数据类别、标准编码 |
| 主数据查询接口 | `mdm_api` | 查询接口 | 接口编号、实体模型、查询字段 |
| 主数据治理看板 | `mdm_dashboard` | 看板配置 | 看板编号、看板名称、监控实体 |

## 目标列冲突校验

一条源记录的两个源字段可能都映射到同一目标列（例如旧工商号与统一
信用代码都写入工商标识）。`data_import_rule` 会为每条原始行生成字段
映射校验明细，逐行给出：

| 场景 | 明细判定 | 目标列 |
| --- | --- | --- |
| 多个来源列非空且值一致 | 一致 | 写入该值 |
| 只有一个来源列有值 | 单列有值 | 写入该值 |
| 两个非空值不一致，规则有来源优先级 | 按规则裁定 | 写入优先级最高的来源值 |
| 两个非空值不一致，规则没有裁定依据 | 冲突 | 留空，该行退回修订 |
| 全部来源列为空 | 无值 | 留空 |

- 明细保留每一行的原值和来源列，冲突行额外记录 `采用值`/`采用来源列`
  为空的原因，导入人可以直接核对两边值。
- `批次导入管理` 以该明细认定核实过的输入范围（`verified_scope`），
  每条原始行只计一次；实际入库数按可入库且未重复的行计，拒绝数按
  冲突未裁定或无法映射的行计，三者之和必须等于原始行数。
- 目标表尚未确认时批次停在 `待确认`；存在拒绝行时批次进入 `待修订`，
  经办人核对后再提交修订批次。
- 目标实体中已确认的客户记录、以及过往已结算批次确认过的标识，再次
  提交时只计入重复数，不会重复入库。

```bash
# 生成字段映射校验明细（入库规则维度）
curl -X POST http://127.0.0.1:8000/api/data_import_rule/3/field-mapping-validation \
  -H 'Content-Type: application/json' \
  -d '{"records":[{"旧工商号":"3100001234567","统一信用代码":"91310000MA1K35Q32X"}]}'

# 按校验明细结算批次（批次导入管理维度）
curl -X POST http://127.0.0.1:8000/api/import_batch/1/settle \
  -H 'Content-Type: application/json' \
  -d '{"rule_id":3,"records":[...],"target_table_confirmed":false}'

# 目标表确认后提交修订批次
curl -X POST http://127.0.0.1:8000/api/import_batch/1/submit-revision \
  -H 'Content-Type: application/json' \
  -d '{"rule_id":3,"records":[...],"target_table_confirmed":true}'
```

字段映射配置支持 `旧工商号->工商标识,统一信用代码->工商标识`、
`旧工商号,统一信用代码->工商标识` 和 `旧工商号+统一信用代码->工商标识`
三种写法；来源优先级写在 `校验规则` 里，例如 `统一信用代码>旧工商号`
或 `以统一信用代码为准`。

## 约定

- 全部模块共享`frontend/src/views/WorkbenchView.vue|tsx`和一套资源路由。
- 每个模块仍保留独立`backend/app/services/<模块>.py`；纯前端保留
  `frontend/src/domain/<模块>.ts`，用于承载真实业务差异并维持36题位容量。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。
- 校验与结算类动作放在 `backend/app/routers/validation.py`，由
  `data_import_rule` / `import_batch` 的领域服务实现，路由只做参数转换。

领域：数据工程
子方向：数仓与数据湖
