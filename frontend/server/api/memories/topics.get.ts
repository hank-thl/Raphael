export default defineEventHandler(async (event) => {
  setResponseHeader(event, 'Cache-Control', 'no-store')
  return proxyRequest(event, new URL('/memories/topics', useRuntimeConfig(event).backendBaseUrl).toString())
})
