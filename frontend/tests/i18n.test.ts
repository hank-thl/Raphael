import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { createI18n } from 'vue-i18n'
import { supportedLocales, normalizeLocale } from '../app/utils/locale.ts'
import { recordErrorMessage, recordValidation } from '../app/utils/records.ts'
import { authErrorMessage } from '../app/utils/auth-errors.ts'
import { recordToMessage } from '../app/utils/chat.ts'

function flatten(value: Record<string, unknown>, prefix = ''): Record<string, string> {
  return Object.assign({}, ...Object.entries(value).map(([key, item]) => {
    const path = prefix ? `${prefix}.${key}` : key
    return typeof item === 'string' ? { [path]: item } : flatten(item as Record<string, unknown>, path)
  }))
}
const catalogs = Object.fromEntries(supportedLocales.map(code => [code, JSON.parse(readFileSync(new URL(`../i18n/locales/${code}.json`, import.meta.url), 'utf8'))]))

test('all four catalogs have complete keys and matching named placeholders', () => {
  const base = flatten(catalogs['zh-TW'])
  for (const code of supportedLocales) {
    const catalog = flatten(catalogs[code])
    assert.deepEqual(Object.keys(catalog).sort(), Object.keys(base).sort())
    for (const key of Object.keys(base)) {
      assert.ok(catalog[key]?.trim(), `${code}:${key}`)
      assert.deepEqual(catalog[key]!.match(/\{\w+\}/g)?.sort(), base[key]!.match(/\{\w+\}/g)?.sort(), `${code}:${key}`)
    }
  }
})

test('default and persisted locale values accept only the supported language codes', () => {
  for (const code of supportedLocales) assert.equal(normalizeLocale(code), code)
  for (const invalid of [undefined, null, '', 'fr', ['ja'], '__proto__']) assert.equal(normalizeLocale(invalid), 'zh-TW')
})

test('Vue I18n compiles every message and localizes safe errors without leaking server details', () => {
  for (const code of supportedLocales) {
    const i18n = createI18n({ legacy: false, locale: code, fallbackLocale: 'zh-TW', messages: catalogs })
    const translate = (key: string, values: Record<string, string | number> = {}) => i18n.global.t(key, values)
    for (const key of Object.keys(flatten(catalogs[code]))) {
      assert.notEqual(translate(key, { stage: 'stage', status: 401, detail: 'detail', count: 2, time: '12:00', action: 'action', account: 'account' }), key)
    }
    assert.equal(recordValidation('', translate), catalogs[code].validation.empty)
    assert.equal(recordErrorMessage({ statusCode: 403 }, translate), catalogs[code].errors.forbidden)
    assert.equal(authErrorMessage({ code: 'auth/popup-blocked' }, 'google', translate), catalogs[code].auth.googleErrors['popup-blocked'])
    const result = authErrorMessage({ statusCode: 503, data: { detail: 'secret-token' } }, 'session', translate)
    assert.ok(!result.includes('secret-token'))
  }
})

test('record presentation preserves multilingual content and whitespace exactly', () => {
  const content = ' 今天\n日本語 English ภาษาไทย 👋  '
  const message = recordToMessage({ id: 'entry', content, created_at: '2026-11-12T12:00:00Z' })
  assert.equal(message.content, content)
})

test('existing feedback follows a language switch without repeating its API action', async () => {
  const { ref } = await import('vue')
  const { useTranslatedMessage } = await import('../app/composables/useTranslatedMessage.ts')
  const language = ref('en')
  const feedback = useTranslatedMessage()
  feedback.set(() => catalogs[language.value].errors.generic)
  assert.equal(feedback.text.value, catalogs.en.errors.generic)
  language.value = 'th'
  assert.equal(feedback.text.value, catalogs.th.errors.generic)
  feedback.clear()
  assert.equal(feedback.text.value, '')
})
