<script setup lang="ts">
import { authErrorMessage, type AuthStage } from '~/utils/auth-errors'
interface CurrentUser { uid: string; email: string | null }

const nuxt = useNuxtApp()
const config = useRuntimeConfig().public.firebase
const configured = Boolean(config.apiKey && config.authDomain && config.projectId && config.appId)
const { data: user, status, error: sessionError, refresh } = await useFetch<CurrentUser>('/api/auth/me')
const ready = ref(false)
const busy = ref(false)
const message = ref('')

async function csrfToken() {
  return (await $fetch<{ csrf_token: string }>('/api/auth/csrf')).csrf_token
}

onMounted(async () => {
  if (!configured) return
  if (!nuxt.$googleLogin) {
    message.value = '登入設定無法初始化，請確認設定後重新載入。'
    return
  }
  try {
    await csrfToken()
    ready.value = true
  } catch (error: unknown) {
    message.value = authErrorMessage(error, 'csrf')
  }
})

async function login() {
  if (busy.value || !ready.value || !nuxt.$googleLogin) return
  busy.value = true
  message.value = ''
  let stage: AuthStage = 'google'
  try {
    const idToken = await nuxt.$googleLogin()
    stage = 'csrf'
    const csrf = await csrfToken()
    stage = 'session'
    await $fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'x-csrf-token': csrf },
      body: { id_token: idToken },
    })
    stage = 'profile'
    await refresh()
    if (!user.value) message.value = authErrorMessage(sessionError.value, 'profile')
  } catch (error: unknown) {
    message.value = authErrorMessage(error, stage)
  } finally {
    busy.value = false
  }
}

async function logout() {
  if (busy.value) return
  busy.value = true
  message.value = ''
  try {
    const csrf = await csrfToken()
    await $fetch('/api/auth/logout', { method: 'POST', headers: { 'x-csrf-token': csrf } })
    await refresh()
  } catch {
    message.value = '登出失敗，請再試一次。'
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <main class="mx-auto flex min-h-dvh max-w-lg flex-col justify-center px-5 py-12">
    <NuxtLink to="/" class="mb-8 w-fit text-teal-800 underline underline-offset-4">回到首頁</NuxtLink>
    <section class="rounded-2xl border border-stone-200 bg-white p-6 shadow-sm" aria-labelledby="login-heading">
      <p class="text-sm font-medium tracking-widest text-teal-700">RAPHAEL</p>
      <h1 id="login-heading" class="mt-3 text-2xl font-semibold">{{ user ? '已登入' : '登入你的紀錄空間' }}</h1>
      <p v-if="status === 'pending'" role="status" class="mt-4">正在確認登入狀態…</p>
      <template v-else-if="user">
        <p class="mt-4 break-all text-stone-600">{{ user.email || 'Google 帳號' }}</p>
        <button type="button" :disabled="busy" class="mt-6 min-h-11 rounded-xl bg-teal-800 px-5 py-3 text-white focus-visible:outline-2 focus-visible:outline-offset-4 disabled:opacity-50" @click="logout">{{ busy ? '處理中…' : '登出' }}</button>
      </template>
      <template v-else>
        <p class="mt-4 leading-7 text-stone-600">目前僅供已授權的 Google 帳號使用。</p>
        <p v-if="!configured" class="mt-4 text-stone-600">登入功能尚未設定完成。</p>
        <p v-else-if="sessionError && sessionError.statusCode !== 401 && !message" role="status" class="mt-4 text-stone-600">暫時無法確認登入狀態。</p>
        <button type="button" :disabled="!ready || busy" class="mt-6 min-h-11 w-full rounded-xl bg-teal-800 px-5 py-3 font-medium text-white focus-visible:outline-2 focus-visible:outline-offset-4 disabled:opacity-50" @click="login">{{ busy ? '登入中…' : '使用 Google 登入' }}</button>
      </template>
      <p v-if="message" role="alert" class="mt-4 leading-7 text-stone-700">{{ message }}</p>
    </section>
  </main>
</template>
