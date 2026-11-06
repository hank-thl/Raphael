import assert from 'node:assert/strict'
import test from 'node:test'
import { authErrorMessage } from '../app/utils/auth-errors.ts'

test('Google errors identify domain, provider restrictions and popup problems', () => {
  assert.match(authErrorMessage({ code: 'auth/unauthorized-domain' }, 'google'), /授權網域/)
  assert.match(authErrorMessage({ code: 'auth/admin-restricted-operation' }, 'google'), /Google 身分連結/)
  assert.match(authErrorMessage({ code: 'auth/popup-blocked' }, 'google'), /允許瀏覽器/)
})

test('backend stage and safe error details remain distinct', () => {
  assert.match(authErrorMessage({ statusCode: 403, data: { detail: '不允許的請求來源' } }, 'session'), /建立登入狀態.*HTTP 403.*不允許的請求來源/)
  assert.match(authErrorMessage({ response: { status: 503 } }, 'csrf'), /登入安全驗證.*HTTP 503/)
})

test('successful exchange followed by unauthorized me points to session persistence', () => {
  assert.match(authErrorMessage({ statusCode: 401 }, 'profile'), /Cookie/)
  assert.doesNotMatch(authErrorMessage({ statusCode: 401 }, 'session'), /Cookie/)
})

test('unknown messages, tokens and server bodies are never displayed', () => {
  const secret = 'private-token-value'
  for (const stage of ['google', 'csrf', 'session', 'profile'] as const) {
    const result = authErrorMessage({ message: secret, code: secret, data: { detail: secret } }, stage)
    assert.ok(!result.includes(secret))
  }
  assert.doesNotThrow(() => authErrorMessage(null, 'google'))
  assert.doesNotThrow(() => authErrorMessage('unknown', 'session'))
})
