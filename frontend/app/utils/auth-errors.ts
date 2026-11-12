import { defaultTranslate, type Translate } from './translate.ts'

export type AuthStage = 'google' | 'csrf' | 'session' | 'profile'

const googleMessages: Record<string, string> = {
  "auth/popup-closed-by-user": "auth.googleErrors.popup-closed-by-user",
  "auth/popup-blocked": "auth.googleErrors.popup-blocked",
  "auth/cancelled-popup-request": "auth.googleErrors.cancelled-popup-request",
  "auth/unauthorized-domain": "auth.googleErrors.unauthorized-domain",
  "auth/operation-not-allowed": "auth.googleErrors.operation-not-allowed",
  "auth/admin-restricted-operation": "auth.googleErrors.admin-restricted-operation",
  "auth/account-exists-with-different-credential": "auth.googleErrors.account-exists-with-different-credential",
  "auth/network-request-failed": "auth.googleErrors.network-request-failed",
  "auth/invalid-api-key": "auth.googleErrors.invalid-api-key",
  "auth/user-disabled": "auth.googleErrors.user-disabled",
  "auth/invalid-credential": "auth.googleErrors.invalid-credential"
}

// Match only known API protocol messages; never display arbitrary server data.
const backendMessages: Record<string, string> = {
  "不允許的請求來源": "auth.backend.d0",
  "請重新載入頁面後再試一次": "auth.backend.d1",
  "此帳號尚未獲得使用權限": "auth.backend.d2",
  "僅允許 Google 登入": "auth.backend.d3",
  "登入功能尚未設定完成": "auth.backend.d4",
  "登入服務暫時無法使用": "auth.backend.d5",
  "登入已失效，請重新登入": "auth.backend.d6",
  "請重新執行 Google 登入": "auth.backend.d7",
  "請先登入": "auth.backend.d8"
}

export function authErrorMessage(error: unknown, stage: AuthStage, t: Translate = defaultTranslate): string {
  const failure = error && typeof error === 'object'
    ? error as { code?: unknown; statusCode?: unknown; response?: { status?: unknown }; data?: { detail?: unknown } }
    : undefined
  if (stage === 'google' && typeof failure?.code === 'string' && Object.hasOwn(googleMessages, failure.code)) {
    return t(googleMessages[failure.code]!)
  }
  const rawStatus = failure?.statusCode ?? failure?.response?.status
  const status = typeof rawStatus === 'number' && Number.isInteger(rawStatus) && rawStatus >= 100 && rawStatus <= 599
    ? rawStatus : undefined
  if (stage === 'profile' && status === 401) {
    return t('auth.cookie')
  }
  const detail = failure?.data?.detail
  const safeDetail = typeof detail === 'string' && Object.hasOwn(backendMessages, detail)
    ? t('auth.detail', { detail: t(backendMessages[detail]!) }) : t('auth.retry')
  return t('auth.failed', { stage: t(`auth.${stage}`), status: status ? t('auth.status', { status }) : '', detail: safeDetail })
}
