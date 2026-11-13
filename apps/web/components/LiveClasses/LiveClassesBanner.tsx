'use client'
// Fork: site-wide banner when a class you can join is live or about to start.
import React, { useEffect, useState } from 'react'
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { X } from 'lucide-react'
import { listLiveClasses } from '@services/plugnmeet/liveClasses'
import { liveClassKeys } from './LiveClassesPage'
import { JoinButton, classHref, relativeStart, useFormatters, useLiveClassContext, useLiveClassesConfig, useNow } from './shared'

const DISMISS_KEY = 'lh_live_class_banner_dismissed'

function readDismissed(): string[] {
  try {
    return JSON.parse(sessionStorage.getItem(DISMISS_KEY) || '[]')
  } catch {
    return []
  }
}

export default function LiveClassesBanner({ orgslug }: { orgslug: string }) {
  const { t } = useTranslation()
  const pathname = usePathname()
  const { orgId, token } = useLiveClassContext()
  const config = useLiveClassesConfig()
  const fmt = useFormatters()
  const now = useNow(20_000)
  const [dismissed, setDismissed] = useState<string[]>([])
  useEffect(() => {
    // Read after hydration so server and first client render agree.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setDismissed(readDismissed())
  }, [])

  const query = useQuery({
    queryKey: liveClassKeys.list(orgId, 'now'),
    queryFn: () => listLiveClasses(orgId, 'now', token, undefined, 5),
    enabled: !!config?.enabled && !!token && orgId > 0,
    refetchInterval: 60_000,
    staleTime: 30_000,
    retry: false,
  })

  const current = (query.data || []).find((c) => !dismissed.includes(c.class_uuid) && (c.can_join || c.can_start || c.status === 'scheduled'))
  if (!current) return null
  // Already on that class's page: the page itself has the button.
  const id = current.class_uuid.replace(/^liveclass_/, '')
  if (pathname?.includes(`/live-classes/${id}`)) return null

  const dismiss = () => {
    const next = [...dismissed, current.class_uuid]
    setDismissed(next)
    try {
      sessionStorage.setItem(DISMISS_KEY, JSON.stringify(next.slice(-20)))
    } catch {
      /* storage unavailable */
    }
  }
  const live = current.status === 'live'

  return (
    <div
      role="status"
      className="fixed inset-x-0 bottom-0 z-40 px-3 pb-3 md:pb-4 pointer-events-none mb-16 md:mb-0"
    >
      <div className="pointer-events-auto mx-auto flex max-w-2xl items-center gap-3 rounded-xl bg-white px-4 py-3 shadow-lg shadow-gray-300/40 outline outline-1 outline-neutral-200/60">
        <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${live ? 'animate-pulse bg-red-500' : 'bg-green-500'}`} aria-hidden />
        <Link href={classHref(orgslug, current)} className="min-w-0 flex-1">
          <p className="text-[10px] font-bold uppercase tracking-wide text-gray-400">
            {live
              ? t('live_classes.banner.live', { defaultValue: 'Live now' })
              : t('live_classes.banner.soon', { when: relativeStart(fmt, current.starts_at, now), defaultValue: `Starts ${relativeStart(fmt, current.starts_at, now)}` })}
          </p>
          <p className="truncate text-sm font-semibold text-gray-900 hover:underline underline-offset-2">{current.title}</p>
        </Link>
        {(current.can_join || current.can_start) && <JoinButton liveClass={current} size="sm" />}
        <button
          type="button"
          onClick={dismiss}
          className="rounded-md p-1 text-gray-400 hover:bg-gray-50 hover:text-gray-700"
          aria-label={t('live_classes.banner.dismiss', { defaultValue: 'Dismiss' })}
        >
          <X className="h-4 w-4" />
        </button>
      </div>
    </div>
  )
}
