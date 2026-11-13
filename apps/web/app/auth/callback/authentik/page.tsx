'use client'

// Fork: landing page for the Authentik (OpenID Connect) redirect. Hands the
// authorization code to the API (which does all validation server-side) and
// then establishes the session exactly like the Google callback does.
import React, { useEffect, useRef, useState } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import Link from 'next/link'
import { AlertTriangle, Loader2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useAuth } from '@components/Contexts/AuthContext'
import { completeAuthentikLogin } from '@services/auth/authentik'
import { removeQueryParamsFromAddressBar } from '@services/utils/ts/scrubUrl'

export default function AuthentikCallbackPage() {
  const { t } = useTranslation()
  const searchParams = useSearchParams()
  const router = useRouter()
  const { signIn } = useAuth()
  const [error, setError] = useState<string | null>(null)
  // The code is single-use; never redeem it twice (StrictMode double effects).
  const handledRef = useRef(false)

  useEffect(() => {
    if (handledRef.current) return
    handledRef.current = true
    const params = new URLSearchParams(searchParams.toString())
    // Keep the code out of history and out of any Referer from here on.
    removeQueryParamsFromAddressBar(['code', 'state', 'iss', 'session_state', 'error', 'error_description'])

    ;(async () => {
      try {
        const data = await completeAuthentikLogin(params)
        const next = data.callback_url || '/'

        if (data.mfa_required && data.mfa_token) {
          const mfaParams = new URLSearchParams({ mfa_token: data.mfa_token })
          if (next !== '/') mfaParams.set('redirect_to', next)
          router.push(`/auth/login?${mfaParams.toString()}`)
          return
        }
        if (!data.tokens?.access_token) throw new Error('Unexpected response from the server.')

        const result = await signIn('credentials', {
          redirect: false,
          sso: 'true',
          sso_access_token: data.tokens.access_token,
          sso_user: JSON.stringify(data.user),
          sso_expiry: data.tokens.expiry,
          callbackUrl: next,
        })
        if (result && !result.ok) throw new Error(result.error || 'Could not start your session.')
        router.push(next)
      } catch (err: any) {
        setError(err?.message || 'Authentik sign-in failed.')
      }
    })()
  }, [searchParams, router, signIn])

  if (error) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50">
        <div className="text-center max-w-md mx-auto p-6">
          <div className="flex justify-center mb-4">
            <div className="p-3 bg-red-100 rounded-full">
              <AlertTriangle className="w-12 h-12 text-red-600" />
            </div>
          </div>
          <h1 className="text-xl font-semibold text-gray-800 mb-2">
            {t('auth.authentik_callback.failed', { defaultValue: 'Sign-in failed' })}
          </h1>
          <p className="text-gray-600 mb-6">{error}</p>
          <div className="space-y-3">
            <Link
              href="/login"
              className="block w-full py-2 px-4 bg-black text-white rounded-md hover:bg-gray-800 transition-colors"
            >
              {t('auth.authentik_callback.try_again', { defaultValue: 'Try again' })}
            </Link>
            <Link
              href="/"
              className="block w-full py-2 px-4 bg-gray-100 text-gray-700 rounded-md hover:bg-gray-200 transition-colors"
            >
              {t('auth.authentik_callback.go_home', { defaultValue: 'Go home' })}
            </Link>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50">
      <div className="text-center">
        <div className="flex justify-center mb-4">
          <Loader2 className="w-12 h-12 text-gray-600 animate-spin" />
        </div>
        <h1 className="text-xl font-semibold text-gray-800 mb-2">
          {t('auth.authentik_callback.completing', { defaultValue: 'Completing sign-in…' })}
        </h1>
      </div>
    </div>
  )
}
