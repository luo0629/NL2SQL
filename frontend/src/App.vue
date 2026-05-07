<script setup lang="ts">
import { computed, ref } from 'vue'

type QueryRow = Record<string, unknown>

type QueryResponse = {
  sql?: string
  params?: unknown[]
  debug?: Record<string, unknown> | null
  notes?: string | string[]
  explanation?: string
  status?: string
  message?: string
  detail?: string
  row_count?: number
  columns?: string[]
  rows?: QueryRow[]
  execution_summary?: string
  error_message?: string
  result?: {
    sql?: string
    notes?: string | string[]
  }
}

type StatusTone = 'idle' | 'loading' | 'success' | 'error'

const prompt = ref('')
const sql = ref('')
const notes = ref<string[]>([])
const columns = ref<string[]>([])
const rows = ref<QueryRow[]>([])
const params = ref<unknown[]>([])
const debugTrace = ref<Record<string, unknown> | null>(null)
const executionSummary = ref('')
const status = ref<StatusTone>('idle')
const statusMessage = ref('等待输入。')
const errorMessage = ref('')

const loading = computed(() => status.value === 'loading')
const visibleRowCount = computed(() => rows.value.length)
const derivedColumns = computed(() => {
  if (columns.value.length > 0) return columns.value
  const firstRow = rows.value[0]
  return firstRow ? Object.keys(firstRow) : []
})
const hasRenderableRows = computed(() => rows.value.length > 0 && derivedColumns.value.length > 0)
const formattedParams = computed(() => (params.value.length > 0 ? JSON.stringify(params.value, null, 2) : '[]'))
const formattedDebug = computed(() => (debugTrace.value ? JSON.stringify(debugTrace.value, null, 2) : ''))
const isIdle = computed(() => status.value === 'idle' && !sql.value && rows.value.length === 0)


function pickText(...values: unknown[]): string {
  for (const value of values) {
    if (typeof value === 'string' && value.trim()) {
      return value.trim()
    }
  }

  return ''
}

function normalizeNotes(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value.map((item) => pickText(item)).filter(Boolean)
  }

  const text = pickText(value)
  return text ? text.split(/\n+/).map((item) => item.trim()).filter(Boolean) : []
}

function extractBodyMessage(body: unknown): string {
  if (!body) return ''

  if (typeof body === 'string') {
    return body.trim()
  }

  if (Array.isArray(body)) {
    return body.map((item) => pickText(item)).filter(Boolean).join('\n')
  }

  if (typeof body === 'object') {
    const record = body as Record<string, unknown>
    return pickText(record.message, record.detail, record.error, record.title, record.error_message)
  }

  return ''
}

function normalizeColumns(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  return value.map((item) => pickText(item)).filter(Boolean)
}

function normalizeRows(value: unknown): QueryRow[] {
  if (!Array.isArray(value)) return []
  return value.filter((item): item is QueryRow => !!item && typeof item === 'object')
}

function renderCell(value: unknown): string {
  if (value == null || value === '') return '—'

  if (typeof value === 'object') {
    const serialized = JSON.stringify(value)
    return serialized.length > 120 ? `${serialized.slice(0, 117)}…` : serialized
  }

  const text = String(value)
  return text.length > 160 ? `${text.slice(0, 157)}…` : text
}

async function readResponseBody(response: Response): Promise<unknown> {
  const contentType = response.headers.get('content-type') ?? ''

  if (contentType.includes('application/json')) {
    return response.json()
  }

  const text = await response.text()

  try {
    return JSON.parse(text)
  } catch {
    return text
  }
}

async function handleSubmit() {
  const question = prompt.value.trim()

  if (!question) {
    status.value = 'error'
    statusMessage.value = '请输入问题。'
    errorMessage.value = '问题不能为空。'
    return
  }

  status.value = 'loading'
  statusMessage.value = '正在生成 SQL…'
  errorMessage.value = ''
  sql.value = ''
  notes.value = []
  columns.value = []
  rows.value = []
  params.value = []
  debugTrace.value = null
  executionSummary.value = ''

  try {
    const response = await fetch('/api/query', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ question }),
    })

    const body = await readResponseBody(response)

    if (!response.ok) {
      throw new Error(extractBodyMessage(body) || `请求失败（${response.status}）。`)
    }

    const data = body as QueryResponse | null
    const generatedSql = pickText(data?.sql, data?.result?.sql)
    const generatedNotes = normalizeNotes(
      data?.notes ?? data?.result?.notes ?? [data?.explanation, data?.status ? `响应模式：${data.status}` : ''],
    )
    const resultColumns = normalizeColumns(data?.columns)
    const resultRows = normalizeRows(data?.rows)
    const responseParams = Array.isArray(data?.params) ? data.params : []
    const responseDebug = data?.debug && typeof data.debug === 'object' ? data.debug : null
    const summary = pickText(data?.execution_summary)

    sql.value = generatedSql || '-- 接口未返回 SQL。'
    notes.value = generatedNotes.length > 0 ? generatedNotes : ['本次响应没有附带说明。']
    columns.value = resultColumns
    rows.value = resultRows
    params.value = responseParams
    debugTrace.value = responseDebug
    executionSummary.value =
      summary ||
      (resultRows.length > 0
        ? `当前展示 ${resultRows.length} 行结果。`
        : '查询执行成功，但没有返回记录。')
    status.value = 'success'
    statusMessage.value = resultRows.length > 0 ? '查询结果已返回。' : '查询完成，但没有返回记录。'
  } catch (error) {
    const message = error instanceof Error ? error.message : '无法连接到查询服务。'
    status.value = 'error'
    statusMessage.value = '生成失败。'
    errorMessage.value = message
  }
}
</script>

<template>
  <main class="page">
    <section class="hero" aria-labelledby="hero-title">
      <div class="hero-copyblock">
        <p class="eyebrow">NL2SQL · Query Workspace</p>
        <h1 id="hero-title">像提问一样，直接生成 SQL。</h1>
        <p class="hero-copy">在同一工作区里输入问题、审阅 SQL，并直接核对真实查询结果。</p>
      </div>
      <div class="hero-actions">
        <a class="hero-button" href="#workspace">开始查询</a>
      </div>
    </section>

    <section id="workspace" class="workspace-section" aria-labelledby="workspace-title">
      <div class="workspace-frame">
        <header class="workspace-header">
          <div>
            <p class="section-kicker">演示工作区</p>
            <h2 id="workspace-title">输入一句话，就能看到 SQL、状态和说明</h2>
          </div>

          <div id="query-status" class="status" :class="`status--${status}`" role="status" aria-live="polite" aria-atomic="true">
            <span class="status-dot" aria-hidden="true"></span>
            <span>{{ statusMessage }}</span>
          </div>
        </header>

        <div class="workspace-grid">
          <aside class="control-rail" aria-labelledby="composer-title">
            <section class="composer composer--primary">
              <header class="composer-header">
                <div>
                  <p class="section-kicker">查询控制</p>
                  <h3 id="composer-title">输入问题并发起查询</h3>
                </div>
                <p class="composer-note">输入区现在作为控制面板存在，主空间优先让给结果工作台。</p>
              </header>

              <form class="composer-form" @submit.prevent="handleSubmit">
                <label class="field" for="nl-query">
                  <span class="field-label">问题</span>
                  <textarea
                    id="nl-query"
                    v-model="prompt"
                    rows="10"
                    :disabled="loading"
                    :aria-describedby="errorMessage ? 'query-hint query-status query-error' : 'query-hint query-status'"
                    placeholder="例如：找出近 90 天收入最高的 10 位客户。"
                    @keydown.ctrl.enter.prevent="handleSubmit"
                    @keydown.meta.enter.prevent="handleSubmit"
                  />
                </label>

                <div class="composer-footer">
                  <p class="hint" id="query-hint">
                    按 <kbd>Ctrl</kbd> + <kbd>Enter</kbd> 或 <kbd>⌘</kbd> + <kbd>Enter</kbd> 运行。
                  </p>

                  <button class="submit-button" type="submit" :disabled="loading">
                    {{ loading ? '生成中…' : '生成 SQL' }}
                  </button>
                </div>

                <p v-if="errorMessage" id="query-error" class="error" role="alert">
                  {{ errorMessage }}
                </p>
              </form>
            </section>
          </aside>

          <section class="result result--secondary data-workspace" :data-state="status" aria-labelledby="result-title" :aria-busy="loading">
            <div class="result-header">
              <div>
                <p class="section-kicker">结果工作台</p>
                <h3 id="result-title">先看真实数据，再核对 SQL 与说明</h3>
              </div>
              <p class="result-note">当前主面板优先服务结果核对，SQL 与说明作为辅助阅读层。</p>
            </div>

            <section class="result-block result-block--primary" aria-labelledby="result-table-title">
              <div class="result-block-header">
                <div>
                  <p class="section-kicker">查询结果</p>
                  <h4 id="result-table-title">结果表格</h4>
                </div>
                <p class="result-summary">{{ executionSummary || '提交后会在这里展示执行摘要。' }}</p>
              </div>

              <div class="result-meta-strip" aria-label="结果摘要信息">
                <p class="result-summary result-summary--primary">{{ executionSummary || statusMessage }}</p>
                <div class="meta-pills">
                  <span class="meta-pill">{{ status === 'success' ? visibleRowCount : '—' }} 行</span>
                  <span class="meta-pill">{{ status === 'success' ? derivedColumns.length : '—' }} 列</span>
                  <span class="meta-pill">{{ status === 'success' ? `${params.length} 参数` : '— 参数' }}</span>
                </div>
              </div>

              <div v-if="status === 'loading'" class="result-loading" aria-hidden="true">
                <span class="loading-bar"></span>
                <span class="loading-bar loading-bar--short"></span>
                <span class="loading-grid"></span>
              </div>
              <p v-else-if="status === 'error'" class="error" role="alert">{{ errorMessage || '查询执行失败。' }}</p>
              <div v-else-if="status === 'success' && hasRenderableRows" class="dataset-block">
                <div class="dataset-toolbar">
                  <p class="dataset-note">当前结果以真实查询数据为主展示，可横向滚动查看更多字段。</p>
                  <p class="dataset-meta">{{ visibleRowCount }} 行 · {{ derivedColumns.length }} 列</p>
                </div>
                <div class="table-shell">
                  <table class="result-table">
                    <thead>
                      <tr>
                        <th v-for="column in derivedColumns" :key="column" scope="col">{{ column }}</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr v-for="(row, rowIndex) in rows" :key="rowIndex">
                        <td v-for="column in derivedColumns" :key="`${rowIndex}-${column}`" :title="renderCell(row[column])">{{ renderCell(row[column]) }}</td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              </div>
              <div v-else-if="status === 'success' && rows.length === 0" class="empty-state-group">
                <p class="empty-state">查询执行成功，但没有返回记录。</p>
                <p class="empty-hint">可以尝试缩短时间范围、放宽筛选条件，或者换一种提问方式。</p>
              </div>
              <div v-else-if="isIdle" class="empty-state-group">
                <p class="empty-state">提交后会在这里展示查询结果。</p>
                <p class="empty-hint">真实结果表格、执行摘要和状态信息会在同一块区域中返回。</p>
              </div>
              <div v-else class="empty-state-group">
                <p class="empty-state">结果结构暂时不完整，已回退到安全展示模式。</p>
                <p class="empty-hint">如果查询已成功但列信息缺失，前端会优先尝试从结果数据中推导列名。</p>
              </div>
            </section>

            <div class="workspace-secondary-grid">
              <section class="sql-review" aria-label="SQL 审阅区">
                <div class="sql-review-header">
                  <div>
                    <p class="section-kicker">SQL 审阅</p>
                    <h4>生成语句</h4>
                  </div>
                  <p class="result-summary">先看结果，再回头核对 SQL 细节。</p>
                </div>
                <div class="sql-card" aria-label="SQL 代码块">
                  <pre>{{ sql || '-- 生成结果会显示在这里。' }}</pre>
                </div>
                <div class="params-card" aria-label="SQL 参数">
                  <p class="section-kicker">Params</p>
                  <pre>{{ formattedParams }}</pre>
                </div>
              </section>

              <section v-if="debugTrace" class="debug-panel" aria-label="调试追踪区">
                <p class="section-kicker">Debug Trace</p>
                <details>
                  <summary>查看阶段调试信息</summary>
                  <pre>{{ formattedDebug }}</pre>
                </details>
              </section>

              <section v-if="notes.length > 0" class="notes" aria-label="结果说明区">
                <p class="section-kicker">说明</p>
                <ul class="notes-list">
                  <li v-for="(note, index) in notes" :key="`${index}-${note}`">{{ note }}</li>
                </ul>
              </section>
            </div>
          </section>
        </div>
      </div>
    </section>

  </main>
</template>
