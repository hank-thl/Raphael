export default defineEventHandler(async (event) => {
  setResponseHeader(event, 'Cache-Control', 'no-store')
  const path = getRouterParam(event, 'path') || ''
  const allowed = (path === 'me' && event.method === 'GET')
    || (path === 'users' && ['GET', 'POST'].includes(event.method))
    || (/^users\/[A-Za-z0-9_-]{1,128}$/.test(path) && event.method === 'PATCH')
  if (!allowed) throw createError({ statusCode: 404 })
  return proxyRequest(event, new URL(`/admin/${path}`, useRuntimeConfig(event).backendBaseUrl).toString())
})
