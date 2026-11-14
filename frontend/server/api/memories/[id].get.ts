export default defineEventHandler(async (event) => {
  setResponseHeader(event, 'Cache-Control', 'no-store')
  const id = getRouterParam(event, 'id')
  if (!id || !/^[a-f0-9]{64}$/.test(id)) throw createError({ statusCode: 404 })
  return proxyRequest(event, new URL(`/memories/${id}`, useRuntimeConfig(event).backendBaseUrl).toString())
})
