export type AuthStage = 'google' | 'csrf' | 'session' | 'profile'

const googleMessages: Record<string, string> = {
  'auth/popup-closed-by-user': '你已取消 Google 登入。',
  'auth/popup-blocked': '請允許瀏覽器開啟 Google 登入視窗，再試一次。',
  'auth/cancelled-popup-request': '已有另一個登入視窗，請完成該視窗後再試。',
  'auth/unauthorized-domain': '此網站主機尚未加入 Firebase 的授權網域，請確認 Authorized domains。',
  'auth/operation-not-allowed': 'Firebase 專案尚未允許 Google 登入，請確認 Google 登入供應商。',
  'auth/admin-restricted-operation': 'Firebase 拒絕這個帳號登入，請確認預建帳號與 Google 身分連結。',
  'auth/account-exists-with-different-credential': '這個信箱已有其他登入身分，需要先確認帳號連結。',
  'auth/network-request-failed': '無法連到 Google／Firebase，請確認網路後重試。',
  'auth/invalid-api-key': 'Firebase 公開設定無法使用，請確認 API Key 與專案。',
  'auth/user-disabled': '此帳號已被停用。',
  'auth/invalid-credential': 'Google 登入憑證無法使用，請重新登入。',
}

const backendMessages = new Set([
  '不允許的請求來源', '請重新載入頁面後再試一次',
  '此帳號尚未獲得使用權限', '僅允許 Google 登入',
  '登入功能尚未設定完成', '登入服務暫時無法使用',
  '登入已失效，請重新登入', '請重新執行 Google 登入', '請先登入',
])

const stageNames: Record<AuthStage, string> = {
  google: 'Google 驗證', csrf: '登入安全驗證', session: '建立登入狀態', profile: '確認登入狀態',
}

export function authErrorMessage(error: unknown, stage: AuthStage): string {
  const failure = error && typeof error === 'object'
    ? error as { code?: unknown; statusCode?: unknown; response?: { status?: unknown }; data?: { detail?: unknown } }
    : undefined
  if (stage === 'google' && typeof failure?.code === 'string' && Object.hasOwn(googleMessages, failure.code)) {
    return googleMessages[failure.code]!
  }
  const rawStatus = failure?.statusCode ?? failure?.response?.status
  const status = typeof rawStatus === 'number' && Number.isInteger(rawStatus) && rawStatus >= 100 && rawStatus <= 599
    ? rawStatus : undefined
  if (stage === 'profile' && status === 401) {
    return '登入已送出，但未保留有效的登入狀態。請確認瀏覽器允許 Cookie，並全程使用同一個網址登入。'
  }
  const detail = failure?.data?.detail
  const safeDetail = typeof detail === 'string' && backendMessages.has(detail) ? `：${detail}` : '，請稍後重試。'
  return `${stageNames[stage]}失敗${status ? `（HTTP ${status}）` : ''}${safeDetail}`
}
