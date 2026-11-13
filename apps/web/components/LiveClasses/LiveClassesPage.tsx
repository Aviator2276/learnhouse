'use client'
// Fork: /live-classes. Happening now, upcoming and past classes, the
// organization's quick meeting room, and scheduling for hosts.
import React, { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import toast from 'react-hot-toast'
import { CalendarPlus, Loader2, LogIn, Radio, RotateCw, Video } from 'lucide-react'
import GeneralWrapperStyled from '@components/Objects/StyledElements/Wrappers/GeneralWrapper'
import TypeOfContentTitle from '@components/Objects/StyledElements/Titles/TypeOfContentTitle'
import Modal from '@components/Objects/StyledElements/Modal/Modal'
import {
  endQuickRoom,
  getLiveClassPermissions,
  getQuickRoom,
  joinQuickRoom,
  listLiveClasses,
  type LiveClass,
} from '@services/plugnmeet/liveClasses'
import ScheduleClassForm from './ScheduleClassForm'
import {
  CARD,
  DANGER_BTN,
  EmptyState,
  LiveClassRow,
  PILL,
  PRIMARY_BTN,
  useJoinErrorMessage,
  useLiveClassContext,
  useLiveClassesConfig,
  useNow,
} from './shared'

type Tab = 'upcoming' | 'past'

export const liveClassKeys = {
  all: ['fork-live-classes'] as const,
  list: (orgId: number, when: string, course?: string) => ['fork-live-classes', 'list', orgId, when, course ?? ''] as const,
  perms: (orgId: number, course?: string) => ['fork-live-classes', 'perms', orgId, course ?? ''] as const,
  quick: (orgId: number) => ['fork-live-classes', 'quick', orgId] as const,
  detail: (uuid: string) => ['fork-live-classes', 'detail', uuid] as const,
}

function QuickRoomCard({ orgId }: { orgId: number }) {
  const { t } = useTranslation()
  const { token } = useLiveClassContext()
  const queryClient = useQueryClient()
  const message = useJoinErrorMessage()
  const [busy, setBusy] = useState<'join' | 'end' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const query = useQuery({
    queryKey: liveClassKeys.quick(orgId),
    queryFn: () => getQuickRoom(orgId, token),
    enabled: !!orgId && !!token,
    refetchInterval: 20_000,
    staleTime: 10_000,
  })
  const room = query.data
  if (!room?.enabled && !query.isError) return null

  const join = async () => {
    setBusy('join')
    setError(null)
    try {
      await joinQuickRoom(orgId, token)
    } catch (e) {
      setError(message(e))
      setBusy(null)
      query.refetch()
    }
  }
  const end = async () => {
    setBusy('end')
    try {
      await endQuickRoom(orgId, token)
      toast.success(t('live_classes.quick.ended', { defaultValue: 'Meeting room closed' }))
      queryClient.invalidateQueries({ queryKey: liveClassKeys.quick(orgId) })
    } catch (e) {
      toast.error(message(e))
    } finally {
      setBusy(null)
    }
  }

  const active = !!room?.active
  return (
    <section className={`${CARD} mb-8 flex flex-col gap-4 p-5 sm:flex-row sm:items-center`} aria-labelledby="quick-room-title">
      <div className={`flex h-12 w-12 shrink-0 items-center justify-center rounded-full ${active ? 'bg-red-50 text-red-600' : 'bg-gray-50 text-gray-400'}`}>
        <Radio className="h-6 w-6" />
      </div>
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex flex-wrap items-center gap-2">
          <h2 id="quick-room-title" className="font-semibold text-gray-900">
            {t('live_classes.quick.title', { defaultValue: 'Meeting room' })}
          </h2>
          {room && (
            <span className={`${PILL} ${active ? 'bg-red-100 text-red-700' : 'bg-gray-100 text-gray-500'}`}>
              {active && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-red-500" aria-hidden />}
              {active
                ? t('live_classes.quick.open', { defaultValue: 'Open' })
                : t('live_classes.quick.closed', { defaultValue: 'Closed' })}
            </span>
          )}
        </div>
        <p className="text-sm text-gray-500">
          {query.isError
            ? message(query.error)
            : room?.can_start
              ? t('live_classes.quick.host_body', { defaultValue: 'Open the room for a quick meeting. Members can join while it is open.' })
              : t('live_classes.quick.member_body', { defaultValue: 'Join when a host opens the room for a quick meeting.' })}
        </p>
        {error && (
          <p role="alert" className="text-xs text-red-600">
            {error}
          </p>
        )}
      </div>
      {room && (
        <div className="flex gap-2 sm:ms-auto">
          {(active || room.can_start) && (
            <button type="button" onClick={join} disabled={!!busy} className={`${PRIMARY_BTN} ${active ? 'bg-red-600 hover:bg-red-700' : ''}`}>
              {busy === 'join' ? <Loader2 className="h-4 w-4 animate-spin" /> : <Video className="h-4 w-4" />}
              {active
                ? t('live_classes.actions.join', { defaultValue: 'Join' })
                : t('live_classes.quick.open_room', { defaultValue: 'Open room' })}
            </button>
          )}
          {active && room.can_start && (
            <button type="button" onClick={end} disabled={!!busy} className={DANGER_BTN}>
              {busy === 'end' && <Loader2 className="h-4 w-4 animate-spin" />}
              {t('live_classes.actions.end', { defaultValue: 'End for everyone' })}
            </button>
          )}
        </div>
      )}
    </section>
  )
}

function ClassList({ items, orgslug, now }: { items: LiveClass[]; orgslug: string; now: Date }) {
  return (
    <ul className="space-y-3">
      {items.map((c) => (
        <LiveClassRow key={c.class_uuid} liveClass={c} orgslug={orgslug} now={now} />
      ))}
    </ul>
  )
}

export default function LiveClassesPage({ orgslug }: { orgslug: string }) {
  const { t } = useTranslation()
  const { orgId, token, authenticated, sessionLoading } = useLiveClassContext()
  const config = useLiveClassesConfig()
  const queryClient = useQueryClient()
  const now = useNow()
  const [tab, setTab] = useState<Tab>('upcoming')
  const [scheduling, setScheduling] = useState(false)
  const ready = !!orgId && !!token && !!config?.enabled

  const perms = useQuery({
    queryKey: liveClassKeys.perms(orgId),
    queryFn: () => getLiveClassPermissions(orgId, token),
    enabled: ready,
    staleTime: 60_000,
  })
  const list = useQuery({
    queryKey: liveClassKeys.list(orgId, tab),
    queryFn: () => listLiveClasses(orgId, tab, token, undefined, 100),
    enabled: ready,
    refetchInterval: tab === 'upcoming' ? 30_000 : false,
    staleTime: 15_000,
    placeholderData: (prev) => prev,
  })

  const title = t('live_classes.title', { defaultValue: 'Live classes' })

  if (!sessionLoading && !authenticated) {
    return (
      <div className="w-full">
        <GeneralWrapperStyled>
          <TypeOfContentTitle title={title} type="live" />
          <div className="flex flex-col items-center justify-center rounded-xl border-2 border-dashed border-gray-100 bg-gray-50/30 px-4 py-12">
            <div className="mb-4 rounded-full bg-white p-4 nice-shadow">
              <LogIn className="h-8 w-8 text-gray-300" strokeWidth={1.5} />
            </div>
            <h1 className="mb-2 text-center text-xl font-bold text-gray-600">
              {t('live_classes.sign_in_title', { defaultValue: 'Sign in to see live classes' })}
            </h1>
            <p className="max-w-xs text-center text-gray-400">
              {t('live_classes.sign_in_body', { defaultValue: 'Live classes are for members of this organization.' })}
            </p>
          </div>
        </GeneralWrapperStyled>
      </div>
    )
  }

  if (config && !config.enabled) {
    return (
      <div className="w-full">
        <GeneralWrapperStyled>
          <TypeOfContentTitle title={title} type="live" />
          <EmptyState title={t('live_classes.disabled', { defaultValue: 'Live classes are not set up on this server.' })} />
        </GeneralWrapperStyled>
      </div>
    )
  }

  const items = list.data || []
  const live = tab === 'upcoming' ? items.filter((c) => c.status === 'live') : []
  const rest = tab === 'upcoming' ? items.filter((c) => c.status !== 'live') : items
  const tabBtn = (active: boolean) =>
    `rounded-md px-3 py-1.5 text-xs font-bold transition-colors ${active ? 'bg-gray-900 text-white' : 'text-gray-500 hover:text-gray-900'}`

  return (
    <div className="w-full">
      <GeneralWrapperStyled>
        <div className="flex flex-col justify-between gap-2 sm:flex-row sm:items-center">
          <TypeOfContentTitle title={title} type="live" />
          {perms.data?.can_schedule && (
            <Modal
              isDialogOpen={scheduling}
              onOpenChange={setScheduling}
              minWidth="sm"
              dialogTitle={t('live_classes.schedule_title', { defaultValue: 'Schedule a live class' })}
              dialogDescription={t('live_classes.schedule_org_description', {
                defaultValue: 'Open to every member of the organization.',
              })}
              dialogContent={
                <ScheduleClassForm
                  orgId={orgId}
                  onCancel={() => setScheduling(false)}
                  onDone={(created) => {
                    setScheduling(false)
                    toast.success(
                      created.length > 1
                        ? t('live_classes.scheduled_many', { count: created.length, defaultValue: `${created.length} classes scheduled` })
                        : t('live_classes.scheduled_one', { defaultValue: 'Class scheduled' })
                    )
                    queryClient.invalidateQueries({ queryKey: liveClassKeys.all })
                  }}
                />
              }
              dialogTrigger={
                <button type="button" className={`${PRIMARY_BTN} self-start sm:self-auto`}>
                  <CalendarPlus className="h-4 w-4" />
                  {t('live_classes.actions.schedule_class', { defaultValue: 'Schedule class' })}
                </button>
              }
            />
          )}
        </div>

        {config?.quick_room && orgId > 0 && <QuickRoomCard orgId={orgId} />}

        <div className="mb-4 mt-2 flex items-center gap-3">
          <div role="tablist" aria-label={title} className="flex items-center gap-1 rounded-lg bg-white p-1 nice-shadow">
            <button type="button" role="tab" aria-selected={tab === 'upcoming'} onClick={() => setTab('upcoming')} className={tabBtn(tab === 'upcoming')}>
              {t('live_classes.tabs.upcoming', { defaultValue: 'Upcoming' })}
            </button>
            <button type="button" role="tab" aria-selected={tab === 'past'} onClick={() => setTab('past')} className={tabBtn(tab === 'past')}>
              {t('live_classes.tabs.past', { defaultValue: 'Past' })}
            </button>
          </div>
          {list.isFetching && <RotateCw className="h-3.5 w-3.5 animate-spin text-gray-300" aria-hidden="true" />}
        </div>

        {list.isError && (
          <div className="mb-4 flex items-center justify-between gap-3 rounded-lg bg-red-50 px-4 py-2.5 text-sm text-red-700">
            <span>{t('live_classes.load_error', { defaultValue: "Live classes couldn't be loaded." })}</span>
            <button type="button" onClick={() => list.refetch()} className="text-xs font-bold uppercase tracking-wide hover:text-red-900">
              {t('live_classes.retry', { defaultValue: 'Retry' })}
            </button>
          </div>
        )}

        {list.isLoading || !config ? (
          <div className="space-y-3">
            {[0, 1, 2].map((i) => (
              <div key={i} className="h-20 animate-pulse rounded-xl bg-white nice-shadow" />
            ))}
          </div>
        ) : (
          <div className="space-y-8">
            {live.length > 0 && (
              <section aria-labelledby="lc-now" className="space-y-3">
                <h2 id="lc-now" className="flex items-center gap-2 text-sm font-bold uppercase tracking-wide text-red-600">
                  <span className="h-2 w-2 animate-pulse rounded-full bg-red-500" aria-hidden />
                  {t('live_classes.happening_now', { defaultValue: 'Happening now' })}
                </h2>
                <ClassList items={live} orgslug={orgslug} now={now} />
              </section>
            )}
            {rest.length > 0 ? (
              <ClassList items={rest} orgslug={orgslug} now={now} />
            ) : (
              live.length === 0 && (
                <EmptyState
                  title={
                    tab === 'upcoming'
                      ? t('live_classes.empty_upcoming', { defaultValue: 'No upcoming live classes' })
                      : t('live_classes.empty_past', { defaultValue: 'No past live classes yet' })
                  }
                  body={
                    tab === 'upcoming'
                      ? t('live_classes.empty_upcoming_body', { defaultValue: 'Scheduled classes also appear on your calendar and on their course page.' })
                      : undefined
                  }
                />
              )
            )}
          </div>
        )}
      </GeneralWrapperStyled>
    </div>
  )
}
