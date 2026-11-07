<script setup lang="ts">
definePageMeta({ middleware: 'auth' })
interface Account { uid: string; email: string | null; enabled: boolean; is_admin: boolean }
const requestFetch = useRequestFetch()
await requestFetch('/api/admin/me').catch((error: { statusCode?: number }) => {
  throw createError({ statusCode: error.statusCode || 503, statusMessage: '無法開啟管理介面' })
})
const { user } = useAuth()
const { data: accounts, error, refresh } = await useAsyncData(`admin-users:${user.value?.uid}`, () => requestFetch<Account[]>('/api/admin/users'))
const email = ref('')
const busy = ref(false)
const message = ref('')
async function write(path: string, method: 'POST' | 'PATCH', body: object) {
  if (busy.value) return
  busy.value = true
  message.value = ''
  try {
    const { csrf_token } = await $fetch<{ csrf_token: string }>('/api/auth/csrf')
    await $fetch(path, { method, body, headers: { 'x-csrf-token': csrf_token }, retry: 0 })
    if (method === 'POST') email.value = ''
    await refresh()
    message.value = error.value ? '操作已完成，但清單讀取失敗；請重新整理。' : '已更新允許清單。'
  } catch {
    message.value = '操作未確認。請重新整理確認結果，或確認管理員登入與 Firebase 設定後重試。'
  } finally { busy.value = false }
}
function toggle(account: Account) {
  if (!window.confirm(`確定${account.enabled ? '停用' : '啟用'} ${account.email || account.uid} 的使用權限？個人紀錄會保留。`)) return
  return write(`/api/admin/users/${encodeURIComponent(account.uid)}`, 'PATCH', { enabled: !account.enabled })
}
</script>

<template>
  <main class="mx-auto min-h-dvh max-w-2xl px-5 py-10">
    <NuxtLink to="/records" class="text-teal-800 underline">回到個人紀錄</NuxtLink>
    <h1 class="mt-6 text-2xl font-semibold">允許帳號管理</h1>
    <p class="mt-3 text-stone-600">新增 Google 信箱以允許使用。停用會阻擋後續存取，保留原有紀錄。</p>
    <form class="mt-6 rounded-2xl border border-stone-200 bg-white p-5" @submit.prevent="write('/api/admin/users', 'POST', { email: email.trim() })">
      <label for="account-email" class="block font-medium">Google 帳號信箱</label>
      <input id="account-email" v-model="email" type="email" required maxlength="254" autocomplete="off" :disabled="busy" class="mt-3 min-h-11 w-full rounded-lg border border-stone-300 p-3 text-base">
      <button :disabled="busy" class="mt-3 min-h-11 rounded-lg bg-teal-800 px-5 py-3 text-white disabled:opacity-50">{{ busy ? '處理中…' : '新增允許帳號' }}</button>
    </form>
    <p v-if="message" role="status" class="mt-4">{{ message }}</p>
    <p v-if="error" role="alert" class="mt-4">無法讀取清單，請確認登入狀態並重新整理。</p>
    <ul class="mt-6 space-y-4">
      <li v-for="account in accounts || []" :key="account.uid" class="rounded-2xl border border-stone-200 bg-white p-5">
        <p class="break-all font-medium">{{ account.email || '既有允許帳號（可用信箱新增以補齊資料）' }}</p>
        <p class="mt-2 break-all text-sm text-stone-500">{{ account.uid }}</p>
        <p class="mt-2">{{ account.is_admin ? '管理員' : account.enabled ? '已允許' : '已停用' }}</p>
        <button v-if="!account.is_admin" :disabled="busy" class="mt-3 min-h-11 rounded-lg border border-stone-400 px-5 py-2 disabled:opacity-50" @click="toggle(account)">{{ account.enabled ? '停用' : '重新啟用' }}</button>
      </li>
    </ul>
  </main>
</template>
