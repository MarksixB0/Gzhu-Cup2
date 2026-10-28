<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../../../shell/api'
import { state, persistSettings } from '../../../shell/store'
import type { ConnectionView } from '../../../types'
import Icon from '../../../shell/components/Icon.vue'

/* ---------- 本地表单状态 ----------
   后端绝不回传明文 Key;故 Key 输入框永远从空开始,保存时留空表示"沿用已存"。 */
const enabled = ref(true)
const baseUrl = ref('')
const apiKey = ref('')
const savedKeySet = ref(false)
const savedKeyMasked = ref('')
const defaultModel = ref('')
const models = ref<string[]>([])
/** 当前实际生效来源:connection | env_or_default */
const source = ref<ConnectionView['source']>('env_or_default')
const parsedActive = ref(false)
/** 是否已展示过已保存连接信息(首次进入直接落到真实态) */
const loaded = ref(false)

const urlError = ref('')
const keyError = ref('')
const modelError = ref('')
const notice = ref<{ kind: 'ok' | 'err'; text: string } | null>(null)
const showKey = ref(false)
const testing = ref(false)
const saving = ref(false)

/** 连接从探测结果里选默认模型:同时落到 store.settings.model(与「模型」页同一来源) */
function pickDefault(m: string) {
  defaultModel.value = m
  if (modelError.value) modelError.value = ''
  if (m) {
    state.settings.model = m
    persistSettings()
  }
}

function baseUrlInput() {
  // 输入后清除旧错误提示
  if (notice.value?.kind === 'ok') notice.value = null
  if (urlError.value) urlError.value = ''
}

function load() {
  api.getConnection().then((v) => {
    enabled.value = v.enabled
    baseUrl.value = v.base_url
    savedKeySet.value = v.api_key_set
    savedKeyMasked.value = v.api_key_masked
    defaultModel.value = v.default_model
    models.value = v.models ?? []
    source.value = v.source
    parsedActive.value = v.active
    loaded.value = true
  })
}

onMounted(load)

/** 连接是否真正"承接模型调用"(enabled 且 url+key+模型齐) */
function computeActive(): boolean {
  return Boolean(
    enabled.value &&
      baseUrl.value.trim() &&
      defaultModel.value.trim() &&
      (savedKeySet.value || apiKey.value.trim()),
  )
}

function refreshActive() {
  parsedActive.value = computeActive()
}

function validateUrl(): boolean {
  if (!baseUrl.value.trim()) {
    urlError.value = '请填写 API Base URL'
    return false
  }
  if (!/^https?:\/\//i.test(baseUrl.value.trim())) {
    urlError.value = 'URL 需以 http:// 或 https:// 开头'
    return false
  }
  urlError.value = ''
  return true
}

async function startTest() {
  if (!validateUrl()) return
  // 用当前表单地址测试连接并抓取可选模型(不落库)
  testing.value = true
  notice.value = null
  try {
    const res = await api.testConnection({ base_url: baseUrl.value, api_key: apiKey.value })
    models.value = res.models
    // 默认模型只在用户此前手动选过、且仍在结果内时保留,否则默认选中首个
    if (!models.value.includes(defaultModel.value)) defaultModel.value = models.value[0] ?? ''
    if (defaultModel.value) modelError.value = ''
    notice.value = { kind: 'ok', text: `连接成功,发现 ${res.models.length} 个模型可供选择。` }
    refreshActive()
  } catch (e) {
    notice.value = { kind: 'err', text: (e as Error).message }
  } finally {
    testing.value = false
  }
}

async function save() {
  if (!validateUrl()) return
  // 至少要有 Key(已存改或新填)
  if (!savedKeySet.value && !apiKey.value.trim()) {
    keyError.value = '请填写 API Key(连接需鉴权)'
    return
  }
  keyError.value = ''
  // 后端不内置默认模型,缺此项对话接口直接 503
  if (!defaultModel.value.trim()) {
    modelError.value = '请填写默认模型 ID(可先测试连接,从探测结果中点选)'
    return
  }
  modelError.value = ''
  saving.value = true
  notice.value = null
  try {
    const v = await api.saveConnection({
      enabled: enabled.value,
      base_url: baseUrl.value,
      api_key: apiKey.value,
      default_model: defaultModel.value,
      models: models.value,
    })
    // 保存后同步回状态
    savedKeySet.value = v.api_key_set
    savedKeyMasked.value = v.api_key_masked
    defaultModel.value = v.default_model
    models.value = v.models ?? []
    source.value = v.source
    apiKey.value = ''
    refreshActive()
    // 若 connection 定义了默认模型而前端聊天仍用本地 model,提示用户在模型页调整
    notice.value = { kind: 'ok', text: '已保存并立即生效' }
  } catch (e) {
    notice.value = { kind: 'err', text: (e as Error).message }
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <section>
    <div class="mb-1 flex items-center justify-between">
      <h3 class="text-[13px] font-medium" style="color: var(--c-text-2)">API 连接</h3>
      <span
        v-if="loaded"
        class="rounded-full px-2 py-0.5 text-[11px]"
        :style="{
          color: parsedActive ? 'var(--c-accent)' : 'var(--c-text-3)',
          background: parsedActive ? 'var(--c-accent-soft)' : 'transparent',
          border: `1px solid ${parsedActive ? 'var(--c-accent)' : 'var(--c-border)'}`,
        }"
      >
        {{ parsedActive ? '已启用' : '未启用' }}
      </span>
    </div>

    <!-- 状态提示 -->
    <div
      v-if="notice"
      class="mb-3 flex items-start gap-2 rounded-lg border px-3 py-2 text-[12px] leading-relaxed"
      :style="{
        borderColor: notice.kind === 'ok' ? 'var(--c-accent)' : 'var(--c-text-3)',
        color: notice.kind === 'ok' ? 'var(--c-accent)' : 'var(--c-text-2)',
        background: 'var(--c-input)',
      }"
    >
      <Icon :name="notice.kind === 'ok' ? 'check' : 'alert'" :size="14" class="mt-0.5 shrink-0" />
      <span>{{ notice.text }}</span>
    </div>

    <div class="space-y-4">
      <!-- API Base URL -->
      <div>
        <label class="mb-1 block text-[12px]" style="color: var(--c-text-2)">
          API Base URL<span style="color: var(--c-text-3)">（需要 OpenAI 兼容接口）</span>
        </label>
        <div class="flex flex-col gap-2">
          <input
            v-model="baseUrl"
            type="text"
            spellcheck="false"
            autocomplete="off"
            list="api-base-url-suggestions"
            class="w-full rounded-xl border px-3 py-2.5 text-[13px] outline-none transition"
            :style="{
              borderColor: urlError ? '#e5484d' : 'var(--c-border)',
              background: 'var(--c-input)',
              color: 'var(--c-text)',
            }"
            placeholder="https://api.example.com/v1"
            @input="baseUrlInput"
          />
          <datalist id="api-base-url-suggestions">
            <option value="https://dashscope.aliyuncs.com/compatible-mode/v1" />
            <option value="https://api.openai.com/v1" />
            <option value="https://generativelanguage.googleapis.com/v1beta/openai" />
            <option value="https://openrouter.ai/api/v1" />
            <option value="http://localhost:11434/v1" />
          </datalist>
        </div>
        <p v-if="urlError" class="mt-1 text-[11px]" style="color: #e5484d">{{ urlError }}</p>
      </div>

      <!-- API Key -->
      <div>
        <label class="mb-1 block text-[12px]" style="color: var(--c-text-2)">API Key</label>
        <div class="flex gap-2">
          <div class="relative min-w-0 flex-1">
            <input
              v-model="apiKey"
              :type="showKey ? 'text' : 'password'"
              spellcheck="false"
              autocomplete="off"
              class="w-full rounded-xl border px-3 py-2.5 pr-9 text-[13px] outline-none transition"
              :style="{
                borderColor: keyError ? '#e5484d' : 'var(--c-border)',
                background: 'var(--c-input)',
                color: 'var(--c-text)',
              }"
              :placeholder="
                savedKeySet ? `已保存(${savedKeyMasked})——留空则沿用,重新输入即替换` : 'sk-…'
              "
            />
            <button
              class="absolute right-2.5 top-1/2 -translate-y-1/2 rounded p-0.5 ui-hover"
              style="color: var(--c-text-3)"
              type="button"
              aria-label="显隐 API Key"
              @click="showKey = !showKey"
            >
              <Icon :name="showKey ? 'eyeSlash' : 'eye'" :size="16" />
            </button>
          </div>
        </div>
        <p v-if="keyError" class="mt-1 text-[11px]" style="color: #e5484d">{{ keyError }}</p>
      </div>

      <!-- 连接状态与动作 -->
      <div class="flex items-center gap-3">
        <label class="flex cursor-pointer items-center gap-2 text-[12px]" style="color: var(--c-text-2)">
          <span
            class="relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition"
            :style="{
              background: enabled ? 'var(--c-accent)' : 'var(--c-border)',
            }"
            role="switch"
            :aria-checked="enabled"
            @click="enabled = !enabled; refreshActive()"
          >
            <span
              class="inline-block h-4 w-4 transform rounded-full bg-white transition"
              :style="{ marginLeft: enabled ? '18px' : '2px' }"
            />
          </span>
          启用此连接的模型访问
        </label>
        <div class="ml-auto flex items-center gap-2">
          <button
            class="flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-[12px] transition ui-hover"
            style="border-color: var(--c-border); color: var(--c-text-2)"
            type="button"
            :disabled="testing"
            @click="startTest"
          >
            <Icon name="refresh" :size="14" :class="{ 'animate-spin': testing }" />
            {{ testing ? '测试中…' : '测试连接' }}
          </button>
          <button
            class="flex items-center gap-1.5 rounded-lg px-3.5 py-1.5 text-[12px] font-medium text-white transition hover:opacity-90"
            style="background: var(--c-accent)"
            type="button"
            :disabled="saving"
            @click="save"
          >
            {{ saving ? '保存中…' : '保存' }}
          </button>
        </div>
      </div>

      <!-- 默认模型 ID:可手填,探测结果仅作候选 -->
      <div>
        <label class="mb-1 block text-[12px]" style="color: var(--c-text-2)">
          默认模型 ID
        </label>
        <input
          v-model="defaultModel"
          type="text"
          autocomplete="off"
          class="w-full rounded-xl border px-3 py-2.5 text-[13px] outline-none transition"
          :style="{
            borderColor: modelError ? '#e5484d' : 'var(--c-border)',
            background: 'var(--c-input)',
            color: 'var(--c-text)',
          }"
          placeholder="按服务商文档填写，如 gpt-4o-mini"
          @input="modelError = ''; refreshActive()"
        />
        <p v-if="modelError" class="mt-1 text-[11px]" style="color: #e5484d">{{ modelError }}</p>
      </div>

      <!-- 探测到的模型候选 -->
      <div v-if="models.length">
        <label class="mb-1 block text-[12px]" style="color: var(--c-text-2)">
          探测到的模型(点选填入上方,并同步到「模型」页)
        </label>
        <div class="flex flex-wrap gap-1.5">
          <button
            v-for="m in models"
            :key="m"
            class="rounded-full border px-2.5 py-1 text-[11px] ui-hover transition"
            :style="{
              borderColor: m === defaultModel ? 'var(--c-accent)' : 'var(--c-border)',
              color: m === defaultModel ? 'var(--c-accent)' : 'var(--c-text-2)',
              background: m === defaultModel ? 'var(--c-accent-soft)' : 'transparent',
            }"
            @click="pickDefault(m)"
          >
            {{ m }}
          </button>
        </div>
      </div>
    </div>
  </section>
</template>
