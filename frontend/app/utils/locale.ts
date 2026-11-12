export const supportedLocales = ['zh-TW', 'ja', 'en', 'th'] as const
export type UiLocale = typeof supportedLocales[number]
export function normalizeLocale(value: unknown): UiLocale {
  return supportedLocales.includes(value as UiLocale) ? value as UiLocale : 'zh-TW'
}
