import { getApp, getApps, initializeApp } from 'firebase/app'
import {
  browserPopupRedirectResolver, GoogleAuthProvider, initializeAuth,
  inMemoryPersistence, signInWithPopup, signOut,
} from 'firebase/auth'

type GoogleLogin = (() => Promise<string>) | null

export default defineNuxtPlugin<{ googleLogin: GoogleLogin }>(() => {
  const config = useRuntimeConfig().public.firebase
  if (!config.apiKey || !config.authDomain || !config.projectId || !config.appId) {
    return { provide: { googleLogin: null } }
  }

  const app = getApps().length ? getApp() : initializeApp(config)
  const auth = initializeAuth(app, {
    persistence: inMemoryPersistence,
    popupRedirectResolver: browserPopupRedirectResolver,
  })
  const provider = new GoogleAuthProvider()
  provider.setCustomParameters({ prompt: 'select_account' })

  return {
    provide: {
      googleLogin: async () => {
        try {
          const result = await signInWithPopup(auth, provider)
          return await result.user.getIdToken()
        } finally {
          await signOut(auth)
        }
      },
    },
  }
})
