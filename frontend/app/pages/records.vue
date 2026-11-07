<script setup lang="ts">
import type { PersonalRecord } from '~/types/record'
import { appendRecordPage, prependRecord, recordErrorMessage, recordErrorStatus, recordSaveErrorMessage, recordValidation } from '~/utils/records'

definePageMeta({ middleware: 'auth' })
useHead({ title: '我的紀錄｜Raphael' })
const auth = useAuth()
const api = useRecords()
const pageUid = auth.user.value?.uid
const { data, status, error, refresh } = await useAsyncData(`records:${pageUid}`, () => api.list())
const draft = ref('')
const busy = ref(false)
const notice = ref('')
const actionError = ref('')
const sessionExpired = ref(false)
const working = computed(() => busy.value || status.value === 'pending')
const needsLogin = computed(() => sessionExpired.value || [401, 403].includes(recordErrorStatus(error.value) ?? 0))

function handleError(failure: unknown) {
  actionError.value = recordErrorMessage(failure)
  sessionExpired.value = [401, 403].includes(recordErrorStatus(failure) ?? 0)
}

async function sameAccount(): Promise<boolean> {
  const current = await auth.check()
  if (current.uid !== pageUid) {
    // Another tab changed accounts: never merge private records across accounts.
    draft.value = ''
    clearNuxtData()
    await navigateTo('/records', { external: true })
    return false
  }
  sessionExpired.value = false
  return true
}

async function save(content: string) {
  if (working.value || recordValidation(content)) return
  busy.value = true
  notice.value = ''
  actionError.value = ''
  try {
    if (!await sameAccount()) return
    const record = await api.create(content)
    draft.value = ''
    if (data.value) {
      data.value = prependRecord(data.value, record)
    } else {
      await refresh()
      if (!data.value) data.value = prependRecord(undefined, record)
    }
    notice.value = '已儲存紀錄。'
  } catch (failure) {
    handleError(failure)
    actionError.value = recordSaveErrorMessage(failure)
  } finally {
    busy.value = false
  }
}

async function loadMore() {
  if (working.value || !data.value?.next_cursor) return
  busy.value = true
  actionError.value = ''
  try {
    if (!await sameAccount()) return
    const page = await api.list(data.value.next_cursor)
    data.value = appendRecordPage(data.value, page)
  } catch (failure) {
    handleError(failure)
  } finally {
    busy.value = false
  }
}

async function remove(record: PersonalRecord) {
  if (working.value || !window.confirm('要刪除這筆紀錄嗎？刪除後會從列表隱藏，內容仍會保留。')) return
  busy.value = true
  notice.value = ''
  actionError.value = ''
  try {
    if (!await sameAccount()) return
    await api.softDelete(record.id)
    if (data.value) data.value = { ...data.value, items: data.value.items.filter(item => item.id !== record.id) }
    notice.value = '紀錄已從列表移除。'
  } catch (failure) {
    handleError(failure)
  } finally {
    busy.value = false
  }
}

async function reload() {
  if (working.value) return
  busy.value = true
  actionError.value = ''
  try {
    if (!await sameAccount()) return
    await refresh()
  } catch (failure) {
    handleError(failure)
  } finally {
    busy.value = false
  }
}

async function logout() {
  if (working.value || (draft.value.trim() && !window.confirm('有尚未儲存的文字，要放棄並登出嗎？'))) return
  busy.value = true
  try {
    await auth.logout()
    draft.value = ''
    await navigateTo('/login')
  } catch (failure) {
    handleError(failure)
  } finally {
    busy.value = false
  }
}

async function recheckOnFocus() {
  if (working.value) return
  try {
    if (!await sameAccount()) return
    if (!data.value) await refresh()
  } catch (failure) {
    if ([401, 403].includes(recordErrorStatus(failure) ?? 0)) {
      data.value = undefined
      clearNuxtData(`records:${pageUid}`)
    }
    handleError(failure)
  }
}

function recheckOnRestore(event: PageTransitionEvent) {
  if (event.persisted) void recheckOnFocus()
}

onMounted(() => {
  window.addEventListener('focus', recheckOnFocus)
  window.addEventListener('pageshow', recheckOnRestore)
})
onBeforeUnmount(() => {
  window.removeEventListener('focus', recheckOnFocus)
  window.removeEventListener('pageshow', recheckOnRestore)
})
</script>

<template>
  <div class="min-h-dvh">
    <header class="border-b border-stone-200 bg-white">
      <div class="mx-auto flex max-w-3xl items-center justify-between gap-4 px-5 py-4 sm:px-8">
        <NuxtLink to="/" class="shrink-0 text-sm font-semibold tracking-widest text-teal-800">RAPHAEL</NuxtLink>
        <div class="flex min-w-0 items-center gap-2">
          <span class="max-w-36 truncate text-xs text-stone-500 sm:max-w-64 sm:text-sm">{{ auth.user.value?.email || '我的帳號' }}</span>
          <button type="button" :disabled="working" class="min-h-11 shrink-0 rounded-lg px-3 text-sm text-stone-600 hover:bg-stone-100 focus-visible:outline-2 focus-visible:outline-teal-700 disabled:opacity-50" @click="logout">登出</button>
        </div>
      </div>
    </header>
    <main class="mx-auto max-w-3xl px-5 py-8 sm:px-8 sm:py-12">
      <h1 class="text-2xl font-semibold sm:text-3xl">我的紀錄</h1>
      <p class="mb-7 mt-3 leading-7 text-stone-500">把此刻寫下來，留給以後的自己。</p>
      <RecordForm v-model="draft" :busy="working" @submit="save" />
      <p v-if="notice" role="status" class="mt-4 rounded-xl bg-teal-50 px-4 py-3 text-sm text-teal-900">{{ notice }}</p>
      <p v-if="actionError" role="alert" class="mt-4 rounded-xl bg-red-50 px-4 py-3 text-sm leading-6 text-red-800">{{ actionError }}</p>
      <NuxtLink v-if="needsLogin" to="/login" target="_blank" rel="noopener" class="mt-3 inline-block min-h-11 py-3 font-medium text-teal-800 underline">在新分頁重新登入</NuxtLink>

      <section aria-labelledby="history-heading" class="mt-10" :aria-busy="working">
        <div class="mb-4 flex items-center justify-between gap-4">
          <h2 id="history-heading" class="text-lg font-semibold">歷史紀錄</h2>
          <button type="button" :disabled="working" class="min-h-11 rounded-lg px-3 text-sm text-teal-800 hover:bg-teal-50 focus-visible:outline-2 focus-visible:outline-teal-700 disabled:opacity-50" @click="reload">重新整理</button>
        </div>
        <p v-if="error" role="alert" class="mb-4 rounded-xl bg-red-50 p-4 text-sm leading-6 text-red-800">{{ recordErrorMessage(error) }} 可按「重新整理」重試。</p>
        <p v-if="status === 'pending' && !data" role="status" class="py-8 text-center text-stone-500">正在載入紀錄…</p>
        <template v-else-if="data">
          <RecordList v-if="data.items.length" :records="data.items" :busy="working" @remove="remove" />
          <p v-else class="rounded-2xl border border-dashed border-stone-300 px-5 py-12 text-center leading-7 text-stone-500">{{ data.next_cursor ? '這一頁的紀錄已移除，可以繼續載入。' : '還沒有紀錄，先寫下今天的一件事吧。' }}</p>
          <button v-if="data.next_cursor" type="button" :disabled="working" class="mt-5 min-h-11 w-full rounded-xl border border-stone-300 bg-white px-5 py-3 font-medium text-teal-800 hover:bg-teal-50 focus-visible:outline-2 focus-visible:outline-teal-700 disabled:opacity-50" @click="loadMore">{{ busy ? '處理中…' : '載入更多' }}</button>
        </template>
      </section>
    </main>
  </div>
</template>
