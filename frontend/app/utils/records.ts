import { defaultTranslate, type Translate } from './translate.ts'
import type { PersonalRecord, RecordPage } from '../types/record.ts'

export function recordCharacterCount(content: string): number {
  return Array.from(content).length
}

export function recordValidation(content: string, t: Translate = defaultTranslate): string {
  if (!content.trim()) return t('validation.empty')
  if (recordCharacterCount(content) > 10000) return t('validation.tooLong')
  return ''
}

export function appendRecordPage(current: RecordPage, incoming: RecordPage): RecordPage {
  const seen = new Set(current.items.map(item => item.id))
  return {
    items: [...current.items, ...incoming.items.filter(item => !seen.has(item.id))],
    next_cursor: incoming.next_cursor,
  }
}

export function prependRecord(current: RecordPage | undefined, record: PersonalRecord): RecordPage {
  return { items: [record, ...(current?.items ?? []).filter(item => item.id !== record.id)], next_cursor: current?.next_cursor ?? null }
}

export function recordErrorStatus(error: unknown): number | undefined {
  if (!error || typeof error !== 'object') return undefined
  const failure = error as { statusCode?: number; response?: { status?: number } }
  return failure.statusCode ?? failure.response?.status
}

export function recordErrorMessage(error: unknown, t: Translate = defaultTranslate): string {
  const status = recordErrorStatus(error)
  if (status === 401) return t('errors.unauthenticated')
  if (status === 403) return t('errors.forbidden')
  if (status === 404) return t('errors.notFound')
  if (status === 422) return t('errors.invalid')
  return t('errors.generic')
}

export function formatRecordTime(value: string, locale = 'zh-TW'): string {
  return new Intl.DateTimeFormat(locale, {
    timeZone: 'Asia/Taipei', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(new Date(value))
}

export function recordSaveErrorMessage(error: unknown, t: Translate = defaultTranslate): string {
  const status = recordErrorStatus(error)
  if (status === undefined || status >= 500) {
    return t('errors.saveUnknown')
  }
  return recordErrorMessage(error, t)
}
