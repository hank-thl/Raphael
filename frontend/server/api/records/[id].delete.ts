export default defineEventHandler(async (event) => {
  setResponseHeader(event, 'Cache-Control', 'no-store')
  const id = getRouterParam(event, 'id')
  if (!id || !/^[0-9a-fA-F]{24}$/.test(id)) {
    throw createError({ statusCode: 404, statusMessage: 'Record not found' })
  }
  const config = useRuntimeConfig(event)
  const target = new URL(`/records/${id}`, config.backendBaseUrl)
  return proxyRequest(event, target.toString())
})
