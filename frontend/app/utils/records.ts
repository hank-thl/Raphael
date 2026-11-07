import type { PersonalRecord, RecordPage } from '../types/record.ts'

export function recordCharacterCount(content: string): number {
  return Array.from(content).length
}

export function recordValidation(content: string): string {
  if (!content.trim()) return '請先寫下一段文字。'
  if (recordCharacterCount(content) > 10000) return '每筆紀錄最多一萬字，請縮短後再儲存。'
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

export function recordErrorMessage(error: unknown): string {
  const status = recordErrorStatus(error)
  if (status === 401) return '登入已過期，請重新登入後再試一次。尚未送出的文字會保留在此頁。'
  if (status === 403) return '這次操作未獲允許，請確認登入帳號，或重新整理後再試。'
  if (status === 404) return '這筆紀錄已不存在，請重新整理列表。'
  if (status === 422) return '內容不符合格式，請確認不是空白，且未超過一萬字。'
  return '暫時無法完成操作，請確認連線後重試。'
}

export function formatRecordTime(value: string): string {
  return new Intl.DateTimeFormat('zh-TW', {
    timeZone: 'Asia/Taipei', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(new Date(value))
}

export function recordSaveErrorMessage(error: unknown): string {
  const status = recordErrorStatus(error)
  if (status === undefined || status >= 500) {
    return '無法確認是否儲存成功。文字尚未清除，請先重新整理列表確認，避免重複儲存。'
  }
  return recordErrorMessage(error)
}
