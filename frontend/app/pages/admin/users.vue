<script setup lang="ts">
const { t } = useI18n()
definePageMeta({ middleware: 'auth' })
interface Account { uid: string; email: string | null; enabled: boolean; is_admin: boolean }
const requestFetch = useRequestFetch()
await requestFetch('/api/admin/me').catch((error: { statusCode?: number }) => {
  throw createError({ statusCode: error.statusCode || 503, statusMessage: 'Service Unavailable', message: t('admin.unavailable') })
})
const { user } = useAuth()
const { data: accounts, error, refresh } = await useAsyncData(`admin-users:${user.value?.uid}`, () => requestFetch<Account[]>('/api/admin/users'))
const email = ref('')
const busy = ref(false)
const { text: message, set: setMessage } = useTranslatedMessage()
async function write(path: string, method: 'POST' | 'PATCH', body: object) {
  if (busy.value) return
  busy.value = true
  setMessage(() => '')
  try {
    const { csrf_token } = await $fetch<{ csrf_token: string }>('/api/auth/csrf')
    await $fetch(path, { method, body, headers: { 'x-csrf-token': csrf_token }, retry: 0 })
    if (method === 'POST') email.value = ''
    await refresh()
    setMessage(() => error.value ? t('admin.refreshFailed') : t('admin.updated'))
  } catch {
    setMessage(() => t('admin.unknown'))
  } finally { busy.value = false }
}
function toggle(account: Account) {
  if (!window.confirm(t('admin.confirm', { action: t(account.enabled ? 'admin.disable' : 'admin.activate'), account: account.email || account.uid }))) return
  return write(`/api/admin/users/${encodeURIComponent(account.uid)}`, 'PATCH', { enabled: !account.enabled })
}
</script>

<template>
  <main class="mx-auto min-h-dvh max-w-2xl px-5 py-10">
    <NuxtLink to="/chat" class="text-teal-800 underline">{{ t('common.backChat') }}</NuxtLink>
    <LanguageSwitcher class="float-right" />
    <h1 class="mt-6 text-2xl font-semibold">{{ t('admin.title') }}</h1>
    <p class="mt-3 text-stone-600">{{ t('admin.description') }}</p>
    <form class="mt-6 rounded-2xl border border-stone-200 bg-white p-5" @submit.prevent="write('/api/admin/users', 'POST', { email: email.trim() })">
      <label for="account-email" class="block font-medium">{{ t('admin.email') }}</label>
      <input id="account-email" v-model="email" type="email" required maxlength="254" autocomplete="off" :disabled="busy" class="mt-3 min-h-11 w-full rounded-lg border border-stone-300 p-3 text-base">
      <button :disabled="busy" class="mt-3 min-h-11 rounded-lg bg-teal-800 px-5 py-3 text-white disabled:opacity-50">{{ busy ? t('common.processing') : t('admin.add') }}</button>
    </form>
    <p v-if="message" role="status" class="mt-4">{{ message }}</p>
    <p v-if="error" role="alert" class="mt-4">{{ t('admin.listFailed') }}</p>
    <ul class="mt-6 space-y-4">
      <li v-for="account in accounts || []" :key="account.uid" class="rounded-2xl border border-stone-200 bg-white p-5">
        <p class="break-all font-medium">{{ account.email || t('admin.legacy') }}</p>
        <p class="mt-2 break-all text-sm text-stone-500">{{ account.uid }}</p>
        <p class="mt-2">{{ account.is_admin ? t('admin.role') : account.enabled ? t('admin.enabled') : t('admin.disabled') }}</p>
        <button v-if="!account.is_admin" :disabled="busy" class="mt-3 min-h-11 rounded-lg border border-stone-400 px-5 py-2 disabled:opacity-50" @click="toggle(account)">{{ account.enabled ? t('admin.disable') : t('admin.enable') }}</button>
      </li>
    </ul>
  </main>
</template>
