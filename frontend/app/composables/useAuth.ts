interface CurrentUser { uid: string; email: string | null }

export function useAuth() {
  // useState is scoped to the Nuxt request; never share a module-level user ref.
  const user = useState<CurrentUser | null>('current-user', () => null)
  const requestFetch = useRequestFetch()

  async function check() {
    try {
      user.value = await requestFetch<CurrentUser>('/api/auth/me')
      return user.value
    } catch (error) {
      user.value = null
      throw error
    }
  }

  async function logout() {
    const { csrf_token } = await $fetch<{ csrf_token: string }>('/api/auth/csrf')
    await $fetch('/api/auth/logout', { method: 'POST', headers: { 'x-csrf-token': csrf_token }, retry: 0 })
    user.value = null
    clearNuxtData()
  }

  return { user, check, logout }
}
