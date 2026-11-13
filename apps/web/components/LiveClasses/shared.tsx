'use client'
// Fork: building blocks shared by the live-class pages, the course panel and
// the live-now banner. Styling follows the calendar and course pages.
import React, { useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { useTranslation } from 'react-i18next'
import { CalendarClock, Loader2, Radio, Repeat, Video } from 'lucide-react'
import { useOrg } from '@components/Contexts/OrgContext'
import { useLHSession } from '@components/Contexts/LHSessionContext'
import { getUriWithOrg } from '@services/config/config'
import { intlLocale } from '@/lib/format'
import {
  getLiveClassesConfig,
  joinLiveClass,
  LiveClassError,
  type LiveClass,
  type LiveClassesConfig,
} from '@services/plugnmeet/liveClasses'

export const PILL = 'inline-flex shrink-0 items-center gap-1 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide rounded-full'
export const CARD = 'bg-white rounded-xl nice-shadow'
export const PRIMARY_BTN =
  'inline-flex items-center justify-center gap-2 h-9 px-4 text-sm font-semibold text-white bg-black rounded-lg hover:bg-gray-800 transition-colors disabled:opacity-50 disabled:cursor-not-allowed'
export const SECONDARY_BTN =
  'inline-flex items-center justify-center gap-2 h-9 px-4 text-sm font-semibold text-gray-700 bg-white rounded-lg nice-shadow hover:bg-gray-50 transition-colors disabled:opacity-50 disabled:cursor-not-allowed'
export const DANGER_BTN =
  'inline-flex items-center justify-center gap-2 h-9 px-4 text-sm font-semibold text-red-600 bg-white rounded-lg nice-shadow hover:bg-red-50 transition-colors disabled:opacity-50 disabled:cursor-not-allowed'

/** Session token and org id for API calls. */
export function useLiveClassContext() {
  const org = useOrg() as any
  const session = useLHSession() as any
  return {
    orgId: typeof org?.id === 'number' ? (org.id as number) : 0,
    orgName: (org?.name as string) || '',
    token: session?.data?.tokens?.access_token as string | undefined,
    authenticated: session?.status === 'authenticated',
    sessionLoading: session?.status === 'loading',
  }
}

/** Instance configuration; `null` until known. */
export function useLiveClassesConfig(): LiveClassesConfig | null {
  const [config, setConfig] = useState<LiveClassesConfig | null>(null)
  useEffect(() => {
    let cancelled = false
    getLiveClassesConfig().then((c) => {
      if (!cancelled) setConfig(c)
    })
    return () => {
      cancelled = true
    }
  }, [])
  return config
}

/** Re-render every `ms` so countdowns and "live" states stay current. */
export function useNow(ms = 30_000): Date {
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), ms)
    return () => clearInterval(id)
  }, [ms])
  return now
}

export function useFormatters() {
  const { i18n } = useTranslation()
  return useMemo(() => {
    const locale = intlLocale(i18n.language)
    return {
      day: new Intl.DateTimeFormat(locale, { weekday: 'short', day: 'numeric', month: 'short' }),
      fullDay: new Intl.DateTimeFormat(locale, { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' }),
      time: new Intl.DateTimeFormat(locale, { hour: 'numeric', minute: '2-digit' }),
      month: new Intl.DateTimeFormat(locale, { month: 'short' }),
      dayNumber: new Intl.DateTimeFormat(locale, { day: 'numeric' }),
      relative: new Intl.RelativeTimeFormat(locale, { numeric: 'auto' }),
    }
  }, [i18n.language])
}

export type Formatters = ReturnType<typeof useFormatters>

/** "in 25 minutes", "in 3 hours", "tomorrow"... */
export function relativeStart(fmt: Formatters, startsAt: string, now: Date): string {
  const minutes = Math.round((new Date(startsAt).getTime() - now.getTime()) / 60_000)
  if (Math.abs(minutes) < 60) return fmt.relative.format(minutes, 'minute')
  const hours = Math.round(minutes / 60)
  if (Math.abs(hours) < 24) return fmt.relative.format(hours, 'hour')
  return fmt.relative.format(Math.round(hours / 24), 'day')
}

export function classHref(orgslug: string, liveClass: Pick<LiveClass, 'class_uuid'>) {
  return getUriWithOrg(orgslug, `/live-classes/${liveClass.class_uuid.replace(/^liveclass_/, '')}`)
}

export function StatusBadge({ status }: { status: LiveClass['status'] }) {
  const { t } = useTranslation()
  const styles: Record<LiveClass['status'], string> = {
    scheduled: 'bg-green-100 text-green-700',
    live: 'bg-red-100 text-red-700',
    ended: 'bg-gray-100 text-gray-500',
    cancelled: 'bg-gray-100 text-gray-400 line-through',
  }
  return (
    <span className={`${PILL} ${styles[status]}`}>
      {status === 'live' && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-red-500" aria-hidden />}
      {t(`live_classes.status.${status}`, { defaultValue: { scheduled: 'Scheduled', live: 'Live', ended: 'Ended', cancelled: 'Cancelled' }[status] })}
    </span>
  )
}

/** The words for a failed join, so a failure is never shown as "not started". */
export function useJoinErrorMessage() {
  const { t } = useTranslation()
  return (error: unknown): string => {
    const code = error instanceof LiveClassError ? error.code : null
    const status = error instanceof LiveClassError ? error.status : 0
    switch (code) {
      case 'NOT_STARTED':
        return t('live_classes.errors.not_started', { defaultValue: "This class hasn't started yet. You can join as soon as a host opens the room." })
      case 'ENDED':
        return t('live_classes.errors.ended', { defaultValue: 'This class has ended.' })
      case 'CANCELLED':
        return t('live_classes.errors.cancelled', { defaultValue: 'This class was cancelled.' })
      case 'TOO_EARLY':
        return t('live_classes.errors.too_early', { defaultValue: "It's too early to open the room. Try again closer to the start time." })
      case 'RATE_LIMITED':
        return t('live_classes.errors.rate_limited', { defaultValue: 'Too many attempts. Please wait a moment and try again.' })
      case 'UNAVAILABLE':
        return t('live_classes.errors.unavailable', { defaultValue: "The meeting server can't be reached right now. Please try again in a moment." })
    }
    if (status === 401) return t('live_classes.errors.sign_in', { defaultValue: 'Sign in to join this class.' })
    if (status === 403 || status === 404)
      return t('live_classes.errors.forbidden', { defaultValue: "You don't have access to this class." })
    return t('live_classes.errors.generic', { defaultValue: 'Something went wrong. Please try again.' })
  }
}

/** Join (participants) or Start (hosts). Errors are shown under the button. */
export function JoinButton({
  liveClass,
  size = 'md',
  onError,
  hideWhenUnavailable = false,
}: {
  liveClass: LiveClass
  size?: 'sm' | 'md'
  onError?: () => void
  /** In lists, show nothing instead of a disabled button. */
  hideWhenUnavailable?: boolean
}) {
  const { t } = useTranslation()
  const { token } = useLiveClassContext()
  const message = useJoinErrorMessage()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const isHost = liveClass.can_moderate
  if (liveClass.status === 'cancelled' || liveClass.status === 'ended') return null
  const label = isHost && liveClass.status !== 'live'
    ? t('live_classes.actions.start', { defaultValue: 'Start class' })
    : t('live_classes.actions.join', { defaultValue: 'Join' })
  const unavailable = isHost ? !liveClass.can_start && !liveClass.can_join : !liveClass.can_join
  if (unavailable && hideWhenUnavailable) return null
  const disabled = busy || unavailable
  const sizing = size === 'sm' ? 'h-8 px-3 text-xs' : ''

  const onClick = async () => {
    setBusy(true)
    setError(null)
    try {
      await joinLiveClass(liveClass.class_uuid, token)
      // Navigation is under way; keep the spinner.
    } catch (e) {
      setError(message(e))
      setBusy(false)
      onError?.()
    }
  }

  return (
    <div className="flex flex-col items-stretch gap-1.5">
      <button
        type="button"
        onClick={onClick}
        disabled={disabled}
        className={`${PRIMARY_BTN} ${sizing} ${liveClass.status === 'live' ? 'bg-red-600 hover:bg-red-700' : ''}`}
      >
        {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Video className="h-4 w-4" />}
        {label}
      </button>
      {error && (
        <p role="alert" className="text-xs text-red-600 max-w-xs">
          {error}
        </p>
      )}
    </div>
  )
}

/** One class in a list: date tile, title, time, course and action. */
export function LiveClassRow({ liveClass, orgslug, now, showCourse = true }: {
  liveClass: LiveClass
  orgslug: string
  now: Date
  showCourse?: boolean
}) {
  const { t } = useTranslation()
  const fmt = useFormatters()
  const start = new Date(liveClass.starts_at)
  const end = new Date(liveClass.ends_at)
  const isUpcoming = liveClass.status === 'scheduled'
  return (
    <li className={`${CARD} flex flex-col gap-3 p-4 sm:flex-row sm:items-center`}>
      <Link href={classHref(orgslug, liveClass)} className="flex min-w-0 flex-1 items-center gap-4 group" data-kbd-item="">
        <div
          className={`flex h-14 w-14 shrink-0 flex-col items-center justify-center rounded-lg ${
            liveClass.status === 'live' ? 'bg-red-50 text-red-700' : 'bg-gray-50 text-gray-800'
          }`}
          aria-hidden
        >
          <span className="text-[10px] font-bold uppercase tracking-wide">{fmt.month.format(start)}</span>
          <span className="text-xl font-bold leading-none">{fmt.dayNumber.format(start)}</span>
        </div>
        <div className="min-w-0 space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="truncate font-semibold text-gray-900 group-hover:underline underline-offset-2">{liveClass.title}</h3>
            <StatusBadge status={liveClass.status} />
            {liveClass.series_uuid && (
              <span className="text-gray-400" title={t('live_classes.recurring', { defaultValue: 'Recurring' })}>
                <Repeat className="h-3.5 w-3.5" aria-label={t('live_classes.recurring', { defaultValue: 'Recurring' })} />
              </span>
            )}
          </div>
          <p className="flex flex-wrap items-center gap-x-2 text-sm text-gray-500">
            <span>
              {fmt.day.format(start)} · {fmt.time.format(start)} – {fmt.time.format(end)}
            </span>
            {isUpcoming && <span className="text-gray-400">({relativeStart(fmt, liveClass.starts_at, now)})</span>}
          </p>
          {showCourse && liveClass.course && <p className="truncate text-xs font-medium text-gray-400">{liveClass.course.name}</p>}
        </div>
      </Link>
      <div className="sm:ms-auto">
        <JoinButton liveClass={liveClass} size="sm" hideWhenUnavailable />
      </div>
    </li>
  )
}

export function EmptyState({ title, body, icon = 'calendar' }: { title: string; body?: string; icon?: 'calendar' | 'radio' }) {
  const Icon = icon === 'radio' ? Radio : CalendarClock
  return (
    <div className="flex flex-col items-center justify-center rounded-xl border-2 border-dashed border-gray-100 bg-gray-50/30 px-4 py-10">
      <div className="mb-3 rounded-full bg-white p-3 nice-shadow">
        <Icon className="h-6 w-6 text-gray-300" strokeWidth={1.5} />
      </div>
      <p className="text-center font-semibold text-gray-600">{title}</p>
      {body && <p className="mt-1 max-w-sm text-center text-sm text-gray-400">{body}</p>}
    </div>
  )
}
