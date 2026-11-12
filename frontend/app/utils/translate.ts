import messages from '../../i18n/locales/zh-TW.json' with { type: 'json' }

export type Translate = (key: string, values?: Record<string, string | number>) => string
// Keep the pure validation helpers usable in tests and outside a Vue component.
export const defaultTranslate: Translate = (key, values = {}) => {
  let text: unknown = messages
  for (const part of key.split('.')) text = (text as Record<string, unknown> | undefined)?.[part]
  return typeof text === 'string' ? text.replace(/\{(\w+)\}/g, (_, name: string) => String(values[name] ?? `{${name}}`)) : key
}
