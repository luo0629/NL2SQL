<script setup lang="ts">
import { computed, ref } from 'vue'

type QueryResponse = {
  sql?: string
  notes?: string | string[]
  explanation?: string
  status?: string
  message?: string
  detail?: string
  result?: {
    sql?: string
    notes?: string | string[]
  }
}

type StatusTone = 'idle' | 'loading' | 'success' | 'error'

const prompt = ref('找出近 90 天收入最高的 10 位客户。')
const sql = ref('')
const notes = ref<string[]>([])
const status = ref<StatusTone>('idle')
const statusMessage = ref('等待输入。')
const errorMessage = ref('')

const loading = computed(() => status.value === 'loading')

const capabilityCards = [
  {
    title: '自然语言提问',
    text: '直接写业务问题，保留时间范围、条件和维度。',
  },
  {
    title: '即时 SQL 输出',
    text: '结果、状态和说明同屏呈现，方便快速核对。',
  },
  {
    title: '轻量演示工作区',
    text: '像看产品演示一样使用，界面干净但交互完整。',
  },
]

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
    return pickText(record.message, record.detail, record.error, record.title)
  }

  return ''
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

    sql.value = generatedSql || '-- 接口未返回 SQL。'
    notes.value = generatedNotes.length > 0 ? generatedNotes : ['本次响应没有附带说明。']
    status.value = 'success'
    statusMessage.value = 'SQL 已生成。'
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
      <p class="eyebrow">NL2SQL · 中文提问，直接生成 SQL</p>
      <h1 id="hero-title">像提问一样，直接生成 SQL。</h1>
      <p class="hero-copy">把业务问题交给模型，得到 SQL、说明和可继续调整的结果。</p>
      <div class="hero-actions">
        <a class="hero-button" href="#workspace">查看演示工作区</a>
        <a class="hero-link" href="#capabilities">了解核心能力</a>
      </div>
      <p class="hero-footnote">同页完成查询、查看和调整，保持节奏干净利落。</p>
    </section>

    <section id="workspace" class="workspace-section" aria-labelledby="workspace-title">
      <div class="workspace-frame">
        <div class="workspace-topbar" aria-hidden="true">
          <div class="workspace-dots">
            <span></span>
            <span></span>
            <span></span>
          </div>
          <p class="workspace-topbar-label">演示工作区</p>
        </div>

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
          <form class="composer composer--primary" @submit.prevent="handleSubmit">
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

          <section class="result result--secondary" :data-state="status" aria-labelledby="result-title" :aria-busy="loading">
            <div class="result-header">
              <div>
                <p class="section-kicker">SQL 输出</p>
                <h3 id="result-title">生成结果</h3>
              </div>
              <p class="result-note">保持同一页内查看和调整。</p>
            </div>

            <div class="sql-card" aria-label="SQL 代码块">
              <pre>{{ sql || '-- 生成结果会显示在这里。' }}</pre>
            </div>

            <div class="notes">
              <p class="section-kicker">说明</p>
              <ul v-if="notes.length > 0" class="notes-list">
                <li v-for="(note, index) in notes" :key="`${index}-${note}`">{{ note }}</li>
              </ul>
              <p v-else class="empty-state">提交后会显示说明。</p>
            </div>
          </section>
        </div>
      </div>
    </section>

    <section id="capabilities" class="capabilities" aria-label="核心能力">
      <article v-for="card in capabilityCards" :key="card.title" class="feature-card feature-card--quiet">
        <p class="section-kicker">核心体验</p>
        <h3>{{ card.title }}</h3>
        <p>{{ card.text }}</p>
      </article>
    </section>
  </main>
</template>
