<script setup lang="ts">
/**
 * 新建 / 修改一件事的弹层。
 *
 * 表单对象由日历视图传入并持有，弹层只改字段、不自己存状态。
 */
import { computed, nextTick, ref, watch } from 'vue'
import type { CalEvent } from '../useCalendar'

export interface CalForm {
  mode: 'create' | 'edit'
  id: string | null
  kind: 'todo' | 'anni'
  title: string
  date: string
  time: string
  yearly: boolean
}

const props = defineProps<{ form: CalForm; error?: string | null; busy?: boolean }>()
const emit = defineEmits<{ save: []; remove: [id: string]; close: [] }>()

const titleEl = ref<HTMLInputElement | null>(null)
const titleError = ref(false)

const isAnni = computed(() => props.form.kind === 'anni')
const hint = computed(() =>
  isAnni.value
    ? '不设具体时刻，那天和它说话时，它会自己想起来。'
    : '桌宠会在相应的时间提醒你待办事项哦~',
)

watch(
  () => props.form.kind,
  (kind) => {
    if (kind === 'anni') {
      props.form.time = ''
    }
    // 纪念日固定每年都记得；提醒事项不循环。表单里没有这个开关，由类型直接决定。
    props.form.yearly = kind === 'anni'
  },
)

function pick(kind: 'todo' | 'anni') {
  props.form.kind = kind
}

function submit() {
  if (props.busy) return
  if (!props.form.title.trim()) {
    titleError.value = true
    titleEl.value?.focus()
    return
  }
  emit('save')
}

nextTick(() => titleEl.value?.focus())
</script>

<template>
  <div class="cal-modal" @click.self="emit('close')">
    <div class="cal-sheet">
      <div class="shead">
        <div class="stitle">{{ form.mode === 'edit' ? '改一下' : '提醒事项' }}</div>
        <button class="sclose" title="关掉" @click="emit('close')">×</button>
      </div>

      <div class="seg">
        <button :class="{ on: form.kind === 'todo' }" @click="pick('todo')">提醒我</button>
        <button :class="{ on: form.kind === 'anni' }" @click="pick('anni')">纪念日</button>
      </div>

      <input
        ref="titleEl"
        v-model="form.title"
        class="field"
        type="text"
        maxlength="30"
        placeholder="要记住的事…"
        :style="titleError ? { borderColor: '#F08A8A' } : undefined"
        @input="titleError = false"
        @keydown.enter="submit"
      />

      <div class="frow">
        <div>
          <label class="fl">日期</label>
          <input v-model="form.date" class="field" type="date" />
        </div>
        <div>
          <label class="fl">时间</label>
          <input v-model="form.time" class="field" type="time" :disabled="isAnni" />
        </div>
      </div>

      <p class="hint">{{ hint }}</p>
      <!-- 保存/删除失败就地显示：弹层不关，用户还能改完再试，不用重录一遍 -->
      <p v-if="error" class="serr">{{ error }}</p>

      <div class="sbtns">
        <button
          v-if="form.mode === 'edit' && form.id"
          class="del"
          :disabled="busy"
          @click="emit('remove', form.id)"
        >
          删掉它
        </button>
        <span class="spacer" />
        <button class="cancel" :disabled="busy" @click="emit('close')">取消</button>
        <button class="ok" :disabled="busy" @click="submit">确定</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 弹层沿用日历纸的配色与硬投影，与网格同一套语言 */
.cal-modal {
  position: fixed;
  inset: 0;
  z-index: 80;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background: rgba(61, 45, 36, 0.42);
}

.cal-sheet {
  width: 470px;
  max-width: 100%;
  padding: 22px 24px 20px;
  border: 2px solid var(--cal-line-2);
  border-radius: 22px;
  background: var(--cal-paper);
  box-shadow: 8px 8px 0 var(--cal-hard);
  animation: cal-pop 0.16s ease-out;
}

@keyframes cal-pop {
  from { transform: translateY(10px) scale(0.975); }
  to { transform: none; }
}

.shead { display: flex; align-items: center; margin-bottom: 16px; }
.stitle { font-size: 26px; line-height: 1; color: var(--cal-wood); letter-spacing: 1px; -webkit-text-stroke: 0.42px currentColor; }
.sclose {
  margin-left: auto; width: 32px; height: 32px; border-radius: 10px;
  display: inline-flex; align-items: center; justify-content: center;
  color: var(--cal-ink-3); font-size: 22px; line-height: 1;
}
.sclose:hover { background: var(--cal-field); color: var(--cal-wood); }

.fl {
  display: block; margin: 0 0 6px 4px;
  font-size: 14px; color: var(--cal-ink-3); letter-spacing: 0.5px;
  -webkit-text-stroke: 0.42px currentColor;
}
.field {
  width: 100%; margin-bottom: 14px; padding: 12px 15px;
  font-family: inherit; font-size: 18px; color: var(--cal-ink);
  background: var(--cal-field); border: 1.5px solid var(--cal-line-2);
  border-radius: 14px; outline: none;
}
.field::placeholder { color: var(--cal-ink-3); }
.field:focus { border-color: var(--cal-peach); background: var(--cal-paper); }
input[type='date'], input[type='time'] {
  font-size: 18px; letter-spacing: 0.5px; background-image: none;
}
input[type='date']::-webkit-datetime-edit,
input[type='time']::-webkit-datetime-edit {
  font-family: inherit; font-size: 18px; color: var(--cal-ink);
}
input[type='date']::-webkit-calendar-picker-indicator,
input[type='time']::-webkit-calendar-picker-indicator { opacity: 0.55; cursor: pointer; }
input[type='time']:disabled { opacity: 0.4; }

.seg {
  display: flex; margin-bottom: 16px; padding: 3px;
  background: var(--cal-field); border-radius: 14px;
}
.seg button {
  flex: 1; padding: 9px 0 8px; border-radius: 11px;
  font-family: inherit; font-size: 17px; color: var(--cal-ink-2);
  transition: background 0.14s, color 0.14s;
  -webkit-text-stroke: 0.42px currentColor;
}
.seg button.on {
  background: var(--cal-paper); color: var(--cal-wood);
  box-shadow: 0 2px 0 rgba(76, 114, 142, 0.16);
}

.frow { display: flex; gap: 12px; }
.frow > div { flex: 1; min-width: 0; }

.hint {
  margin: 0 0 4px 4px;
  font-size: 14px; line-height: 1.55; color: var(--cal-ink-3);
  -webkit-text-stroke: 0.42px currentColor;
}

.sbtns { display: flex; align-items: center; gap: 10px; margin-top: 18px; }
.spacer { flex: 1; }
.sbtns button { font-family: inherit; }
.sbtns button:disabled { opacity: 0.55; cursor: default; }
.serr {
  margin: 8px 0 0 4px;
  font-size: 13px; line-height: 1.5; color: #b3524f;
  -webkit-text-stroke: 0.42px currentColor;
}
.del { font-size: 15px; color: #c25b5b; padding: 8px 6px; }
.del:hover { text-decoration: underline; }
.cancel, .ok { padding: 10px 24px 9px; border-radius: 999px; font-size: 16px; -webkit-text-stroke: 0.42px currentColor; }
.cancel { color: var(--cal-ink-2); border: 2px solid var(--cal-line-2); background: var(--cal-paper); }
.cancel:hover { background: var(--cal-field); }
.ok { color: #fff; background: var(--cal-peach); border: 2px solid var(--cal-peach); box-shadow: 0 3px 0 #5c8dae; }
.ok:hover { background: #8fbad8; }
</style>
