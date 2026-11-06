const routes = {
  csrf: 'GET',
  login: 'POST',
  logout: 'POST',
  me: 'GET',
} as const

export default defineEventHandler(async (event) => {
  setResponseHeader(event, 'Cache-Control', 'no-store')
  const action = getRouterParam(event, 'action') as keyof typeof routes
  if (!Object.hasOwn(routes, action)) {
    throw createError({ statusCode: 404 })
  }
  if (event.method !== routes[action]) {
    throw createError({ statusCode: 405 })
  }
  const config = useRuntimeConfig(event)
  // Fixed paths only. Origin, CSRF header, cookies and Set-Cookie are forwarded.
  const target = new URL(`/auth/${action}`, config.backendBaseUrl)
  return proxyRequest(event, target.toString())
})
