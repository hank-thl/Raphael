import type { ChatTransport } from '~/types/chat'
import { recordPageToHistory, recordToMessage } from '~/utils/chat'

export function useChatRecords(): ChatTransport {
  const api = useRecords()
  return {
    async history(cursor) { return recordPageToHistory(await api.list(cursor)) },
    softDelete: api.softDelete,
    async send(message) { return recordToMessage(await api.create(message.content)) },
  }
}
