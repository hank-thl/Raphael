import type { PersonalRecord, RecordPage } from '~/types/record'

export function useRecords() {
  const requestFetch = useRequestFetch()

  async function csrfHeaders() {
    const { csrf_token } = await $fetch<{ csrf_token: string }>('/api/auth/csrf')
    return { 'x-csrf-token': csrf_token }
  }

  function list(cursor?: string) {
    return requestFetch<RecordPage>('/api/records', { query: { limit: 20, ...(cursor ? { cursor } : {}) } })
  }

  async function create(content: string) {
    return $fetch<PersonalRecord>('/api/records', {
      method: 'POST', headers: await csrfHeaders(), body: { content }, retry: 0,
    })
  }

  async function softDelete(id: string) {
    await $fetch(`/api/records/${encodeURIComponent(id)}`, {
      method: 'DELETE', headers: await csrfHeaders(), retry: 0,
    })
  }

  return { list, create, softDelete }
}
