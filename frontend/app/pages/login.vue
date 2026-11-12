<script setup lang="ts">
import { authErrorMessage, type AuthStage } from '~/utils/auth-errors'
const { t } = useI18n()
interface CurrentUser { uid: string; email: string | null }

const translate = (key: string, values?: Record<string, string | number>) => t(key, values || {})
const nuxt = useNuxtApp()
const config = useRuntimeConfig().public.firebase
const configured = Boolean(config.apiKey && config.authDomain && config.projectId && config.appId)
const { data: user, status, error: sessionError, refresh } = await useFetch<CurrentUser>('/api/auth/me')
if (user.value) await navigateTo('/chat', { replace: true })
const ready = ref(false)
const busy = ref(false)
const { text: message, set: setMessage } = useTranslatedMessage()

async function csrfToken() {
  return (await $fetch<{ csrf_token: string }>('/api/auth/csrf')).csrf_token
}

onMounted(async () => {
  if (!configured) return
  if (!nuxt.$googleLogin) {
    setMessage(() => t('login.initFailed'))
    return
  }
  try {
    await csrfToken()
    ready.value = true
  } catch (error: unknown) {
    setMessage(() => authErrorMessage(error, 'csrf', translate))
  }
})

async function login() {
  if (busy.value || !ready.value || !nuxt.$googleLogin) return
  busy.value = true
  setMessage(() => '')
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
    if (user.value) await navigateTo('/chat', { replace: true })
    else setMessage(() => authErrorMessage(sessionError.value, 'profile', translate))
  } catch (error: unknown) {
    setMessage(() => authErrorMessage(error, stage, translate))
  } finally {
    busy.value = false
  }
}

</script>

<template>
  <main class="mx-auto flex min-h-dvh max-w-lg flex-col justify-center px-5 py-12">
    <LanguageSwitcher class="mb-4 self-end" />
    <NuxtLink to="/" class="mb-8 w-fit text-teal-800 underline underline-offset-4">{{ t('common.home') }}</NuxtLink>
    <section class="rounded-2xl border border-stone-200 bg-white p-6 shadow-sm" aria-labelledby="login-heading">
      <p class="text-sm font-medium tracking-widest text-teal-700">RAPHAEL</p>
      <h1 id="login-heading" class="mt-3 text-2xl font-semibold">{{ user ? t('login.signedIn') : t('login.heading') }}</h1>
      <p v-if="status === 'pending'" role="status" class="mt-4">{{ t('login.checking') }}</p>
      <p v-else-if="user" role="status" class="mt-4">{{ t('common.openingChat') }}</p>
      <template v-else>
        <p class="mt-4 leading-7 text-stone-600">{{ t('login.allowedOnly') }}</p>
        <p v-if="!configured" class="mt-4 text-stone-600">{{ t('login.notConfigured') }}</p>
        <p v-else-if="sessionError && sessionError.statusCode !== 401 && !message" role="status" class="mt-4 text-stone-600">{{ t('login.unavailable') }}</p>
        <button type="button" :disabled="!ready || busy" class="mt-6 min-h-11 w-full rounded-xl bg-teal-800 px-5 py-3 font-medium text-white focus-visible:outline-2 focus-visible:outline-offset-4 disabled:opacity-50" @click="login">{{ busy ? t('login.signingIn') : t('login.google') }}</button>
      </template>
      <p v-if="message" role="alert" class="mt-4 leading-7 text-stone-700">{{ message }}</p>
    </section>
  </main>
</template>
