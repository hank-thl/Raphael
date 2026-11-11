import type { ChatTransport } from '~/types/chat'
import { recordPageToHistory, recordToMessage } from '~/utils/chat'

export function useChatRecords(): ChatTransport {
  const api = useRecords()
  const expectedUid = useAuth().user.value?.uid
  return {
    async history(cursor) { return recordPageToHistory(await api.list(cursor, expectedUid)) },
    softDelete: api.softDelete,
    async send(message) { return recordToMessage(await api.create(message.content)) },
  }
}
