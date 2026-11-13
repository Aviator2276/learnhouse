'use client'

// Fork: "Sign in with Authentik" button, shown only when the instance has
// Authentik configured AND the org allows the method.
import React, { useEffect, useState } from 'react'
import { KeyRound } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { isAuthMethodAllowed } from '@services/auth/authMethods'
import { getAuthentikConfig, startAuthentikLogin, type AuthentikConfig } from '@services/auth/authentik'

/** Whether to offer Authentik on this org's auth pages. */
export function useAuthentikAvailable(org: any): AuthentikConfig {
  const [config, setConfig] = useState<AuthentikConfig>({ enabled: false })
  useEffect(() => {
    let cancelled = false
    getAuthentikConfig().then((c) => {
      if (!cancelled) setConfig(c)
    })
    return () => {
      cancelled = true
    }
  }, [])
  const allowed = isAuthMethodAllowed(org, 'authentik')
  return { ...config, enabled: config.enabled && allowed }
}

interface Props {
  org: any
  /** Relative path to land on afterwards (absolute same-origin URLs are reduced to their path). */
  next?: string | (() => string)
  inviteCode?: string
  disabled?: boolean
  label?: string
}

function toRelative(next: string | undefined): string | undefined {
  if (!next) return undefined
  try {
    const u = new URL(next, window.location.origin)
    if (u.origin !== window.location.origin) return undefined
    return `${u.pathname}${u.search}${u.hash}`
  } catch {
    return undefined
  }
}

export default function AuthentikSignInButton({ org, next, inviteCode, disabled, label }: Props) {
  const { t } = useTranslation()
  const config = useAuthentikAvailable(org)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  if (!config.enabled) return null

  const onClick = async () => {
    setBusy(true)
    setError(null)
    try {
      const result = await startAuthentikLogin({
        orgId: typeof org?.id === 'number' ? org.id : undefined,
        inviteCode,
        next: toRelative(typeof next === 'function' ? next() : next),
      })
      if (result && !result.ok) {
        setError(result.error)
        setBusy(false)
      }
    } catch {
      setError(t('auth.authentik.start_failed', { defaultValue: 'Could not reach the sign-in service.' }))
      setBusy(false)
    }
  }

  const name = config.display_name || 'Authentik'
  return (
    <div className="space-y-1.5">
      <button
        type="button"
        onClick={onClick}
        disabled={disabled || busy}
        className="flex justify-center items-center w-full bg-white hover:bg-neutral-50 text-black space-x-3 font-medium p-3 rounded-lg border border-neutral-200 transition-all text-sm disabled:opacity-50"
      >
        <KeyRound size={16} />
        <span>
          {busy
            ? t('common.loading')
            : label || t('auth.sign_in_with_authentik', { defaultValue: 'Sign in with {{name}}', name })}
        </span>
      </button>
      {error && <p className="text-xs text-red-600 text-center">{error}</p>}
    </div>
  )
}
