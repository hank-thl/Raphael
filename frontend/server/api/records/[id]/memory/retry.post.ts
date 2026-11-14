export default defineEventHandler(async (event) => {
  setResponseHeader(event, 'Cache-Control', 'no-store')
  const id = getRouterParam(event, 'id')
  if (!id || !/^[0-9a-fA-F]{24}$/.test(id)) throw createError({ statusCode: 404 })
  return proxyRequest(event, new URL(`/records/${id}/memory/retry`, useRuntimeConfig(event).backendBaseUrl).toString())
})
