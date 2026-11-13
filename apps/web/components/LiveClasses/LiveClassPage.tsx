'use client'
// Fork: /live-classes/[id]. One class: when, what, join/start, and for hosts
// editing, ending, cancelling and attendance.
import React, { useState } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import toast from 'react-hot-toast'
import { ArrowLeft, BookCopy, CalendarX, Clock, Loader2, Pencil, Repeat, Trash2, Users } from 'lucide-react'
import GeneralWrapperStyled from '@components/Objects/StyledElements/Wrappers/GeneralWrapper'
import Modal from '@components/Objects/StyledElements/Modal/Modal'
import LinkifiedText from '@components/Objects/StyledElements/LinkifiedText/LinkifiedText'
import { getUriWithOrg } from '@services/config/config'
import {
  cancelLiveClass,
  deleteLiveClass,
  endLiveClass,
  getAttendance,
  getLiveClass,
  LiveClassError,
  type LiveClass,
  type Scope,
} from '@services/plugnmeet/liveClasses'
import ScheduleClassForm from './ScheduleClassForm'
import { liveClassKeys } from './LiveClassesPage'
import {
  CARD,
  DANGER_BTN,
  EmptyState,
  JoinButton,
  PRIMARY_BTN,
  SECONDARY_BTN,
  StatusBadge,
  relativeStart,
  useFormatters,
  useJoinErrorMessage,
  useLiveClassContext,
  useLiveClassesConfig,
  useNow,
} from './shared'

function ScopedAction({
  liveClass,
  kind,
  onConfirm,
}: {
  liveClass: LiveClass
  kind: 'cancel' | 'delete'
  onConfirm: (scope: Scope) => Promise<void>
}) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [scope, setScope] = useState<Scope>('this')
  const [busy, setBusy] = useState(false)
  const isDelete = kind === 'delete'
  const run = async () => {
    setBusy(true)
    try {
      await onConfirm(scope)
      setOpen(false)
    } finally {
      setBusy(false)
    }
  }
  return (
    <Modal
      isDialogOpen={open}
      onOpenChange={setOpen}
      dialogTitle={
        isDelete
          ? t('live_classes.delete_title', { defaultValue: 'Delete class' })
          : t('live_classes.cancel_title', { defaultValue: 'Cancel class' })
      }
      dialogContent={
        <div className="space-y-4 p-1">
          <p className="text-sm text-gray-600">
            {isDelete
              ? t('live_classes.delete_body', { defaultValue: 'The class and its attendance are removed for good.' })
              : t('live_classes.cancel_body', { defaultValue: 'Members see it as cancelled. Its attendance is kept.' })}
          </p>
          {liveClass.series_uuid && (
            <fieldset className="space-y-2">
              {(['this', 'following'] as Scope[]).map((s) => (
                <label key={s} className="flex items-center gap-2 text-sm text-gray-700">
                  <input type="radio" name={`scope-${kind}`} checked={scope === s} onChange={() => setScope(s)} className="accent-black" />
                  {s === 'this'
                    ? t('live_classes.scope.this', { defaultValue: 'This class only' })
                    : t('live_classes.scope.following', { defaultValue: 'This and following classes' })}
                </label>
              ))}
            </fieldset>
          )}
          <div className="flex justify-end gap-2 border-t border-gray-100 pt-4">
            <button type="button" className={SECONDARY_BTN} onClick={() => setOpen(false)} disabled={busy}>
              {t('live_classes.actions.keep', { defaultValue: 'Keep it' })}
            </button>
            <button type="button" className={`${PRIMARY_BTN} bg-red-600 hover:bg-red-700`} onClick={run} disabled={busy}>
              {busy && <Loader2 className="h-4 w-4 animate-spin" />}
              {isDelete
                ? t('live_classes.actions.delete', { defaultValue: 'Delete' })
                : t('live_classes.actions.cancel_class', { defaultValue: 'Cancel class' })}
            </button>
          </div>
        </div>
      }
      dialogTrigger={
        <button type="button" className={isDelete ? DANGER_BTN : SECONDARY_BTN}>
          {isDelete ? <Trash2 className="h-4 w-4" /> : <CalendarX className="h-4 w-4" />}
          {isDelete
            ? t('live_classes.actions.delete', { defaultValue: 'Delete' })
            : t('live_classes.actions.cancel_class', { defaultValue: 'Cancel class' })}
        </button>
      }
    />
  )
}

function Attendance({ liveClass }: { liveClass: LiveClass }) {
  const { t } = useTranslation()
  const { token } = useLiveClassContext()
  const fmt = useFormatters()
  const query = useQuery({
    queryKey: ['fork-live-classes', 'attendance', liveClass.class_uuid],
    queryFn: () => getAttendance(liveClass.class_uuid, token),
    enabled: !!token,
    refetchInterval: liveClass.status === 'live' ? 20_000 : false,
  })
  const attendees = query.data?.attendees || []
  return (
    <section className={`${CARD} p-5`} aria-labelledby="lc-attendance">
      <h2 id="lc-attendance" className="mb-3 flex items-center gap-2 font-semibold text-gray-900">
        <Users className="h-4 w-4 text-gray-400" />
        {t('live_classes.attendance.title', { defaultValue: 'Attendance' })}
        {attendees.length > 0 && <span className="text-sm font-normal text-gray-400">({attendees.length})</span>}
      </h2>
      {query.isError ? (
        <p className="text-sm text-red-600">{t('live_classes.load_error', { defaultValue: "Live classes couldn't be loaded." })}</p>
      ) : attendees.length === 0 ? (
        <p className="text-sm text-gray-400">{t('live_classes.attendance.empty', { defaultValue: 'Nobody has joined yet.' })}</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-100 text-start text-xs uppercase tracking-wide text-gray-400">
                <th className="py-2 pe-4 text-start font-semibold">{t('live_classes.attendance.name', { defaultValue: 'Name' })}</th>
                <th className="py-2 pe-4 text-start font-semibold">{t('live_classes.attendance.joined', { defaultValue: 'Joined' })}</th>
                <th className="py-2 pe-4 text-start font-semibold">{t('live_classes.attendance.time', { defaultValue: 'Time in class' })}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-50">
              {attendees.map((a) => (
                <tr key={a.user_uuid}>
                  <td className="py-2 pe-4">
                    <span className="font-medium text-gray-900">{a.name}</span>{' '}
                    <span className="text-gray-400">@{a.username}</span>
                    {a.in_room && (
                      <span className="ms-2 inline-flex items-center gap-1 text-xs font-semibold text-red-600">
                        <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-red-500" aria-hidden />
                        {t('live_classes.attendance.in_room', { defaultValue: 'In the room' })}
                      </span>
                    )}
                  </td>
                  <td className="py-2 pe-4 text-gray-500">{fmt.time.format(new Date(a.first_joined_at))}</td>
                  <td className="py-2 pe-4 text-gray-500">
                    {t('live_classes.attendance.minutes', { count: a.total_minutes, defaultValue: `${a.total_minutes} min` })}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

export default function LiveClassPage({ orgslug, classId }: { orgslug: string; classId: string }) {
  const { t } = useTranslation()
  const router = useRouter()
  const queryClient = useQueryClient()
  const { token, authenticated, sessionLoading } = useLiveClassContext()
  const config = useLiveClassesConfig()
  const fmt = useFormatters()
  const now = useNow()
  const message = useJoinErrorMessage()
  const [editing, setEditing] = useState(false)
  const [ending, setEnding] = useState(false)
  const backHref = getUriWithOrg(orgslug, '/live-classes')

  const query = useQuery({
    queryKey: liveClassKeys.detail(classId),
    queryFn: () => getLiveClass(classId, token),
    enabled: !!token && !!config?.enabled,
    refetchInterval: 15_000,
    retry: (count, error) => !(error instanceof LiveClassError && error.status < 500) && count < 2,
  })
  const liveClass = query.data
  const refresh = () => queryClient.invalidateQueries({ queryKey: liveClassKeys.all })

  const back = (
    <Link href={backHref} className="mb-4 inline-flex items-center gap-1.5 text-sm font-medium text-gray-500 hover:text-gray-900">
      <ArrowLeft className="h-4 w-4" />
      {t('live_classes.title', { defaultValue: 'Live classes' })}
    </Link>
  )

  if (!sessionLoading && !authenticated) {
    return (
      <GeneralWrapperStyled>
        {back}
        <EmptyState
          title={t('live_classes.sign_in_title', { defaultValue: 'Sign in to see live classes' })}
          body={t('live_classes.sign_in_body', { defaultValue: 'Live classes are for members of this organization.' })}
        />
      </GeneralWrapperStyled>
    )
  }
  if (config && !config.enabled) {
    return (
      <GeneralWrapperStyled>
        {back}
        <EmptyState title={t('live_classes.disabled', { defaultValue: 'Live classes are not set up on this server.' })} />
      </GeneralWrapperStyled>
    )
  }
  if (query.isError) {
    const missing = query.error instanceof LiveClassError && query.error.status === 404
    return (
      <GeneralWrapperStyled>
        {back}
        <EmptyState
          title={
            missing
              ? t('live_classes.not_found', { defaultValue: "This class doesn't exist or isn't open to you." })
              : t('live_classes.load_error', { defaultValue: "Live classes couldn't be loaded." })
          }
        />
      </GeneralWrapperStyled>
    )
  }
  if (!liveClass) {
    return (
      <GeneralWrapperStyled>
        {back}
        <div className="h-64 animate-pulse rounded-xl bg-white nice-shadow" />
      </GeneralWrapperStyled>
    )
  }

  const start = new Date(liveClass.starts_at)
  const end = new Date(liveClass.ends_at)
  const host = liveClass.can_moderate

  const endForEveryone = async () => {
    setEnding(true)
    try {
      await endLiveClass(liveClass.class_uuid, token)
      toast.success(t('live_classes.ended_toast', { defaultValue: 'Class ended for everyone' }))
      refresh()
    } catch (e) {
      toast.error(message(e))
    } finally {
      setEnding(false)
    }
  }

  return (
    <div className="w-full">
      <GeneralWrapperStyled>
        {back}
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
          <div className="space-y-6 lg:col-span-2">
            <article className={`${CARD} p-6`}>
              <div className="mb-3 flex flex-wrap items-center gap-2">
                <StatusBadge status={liveClass.status} />
                {liveClass.series_uuid && (
                  <span className="inline-flex items-center gap-1 text-xs font-medium text-gray-400">
                    <Repeat className="h-3.5 w-3.5" />
                    {t('live_classes.recurring', { defaultValue: 'Recurring' })}
                  </span>
                )}
              </div>
              <h1 className="text-2xl font-bold tracking-tight text-gray-900 md:text-3xl">{liveClass.title}</h1>
              <div className="mt-3 space-y-1.5 text-sm text-gray-600">
                <p className="flex items-start gap-2">
                  <Clock className="mt-0.5 h-4 w-4 shrink-0 text-gray-400" />
                  <span>
                    <span className="first-letter:uppercase inline-block">{fmt.fullDay.format(start)}</span>
                    {' · '}
                    <span className="whitespace-nowrap">
                      {fmt.time.format(start)} – {fmt.time.format(end)}
                    </span>
                    {liveClass.status === 'scheduled' && (
                      <span className="whitespace-nowrap text-gray-400"> ({relativeStart(fmt, liveClass.starts_at, now)})</span>
                    )}
                  </span>
                </p>
                {liveClass.course && (
                  <p className="flex items-center gap-2">
                    <BookCopy className="h-4 w-4 text-gray-400" />
                    <Link
                      href={getUriWithOrg(orgslug, `/course/${liveClass.course.course_uuid.replace(/^course_/, '')}`)}
                      className="font-medium hover:underline underline-offset-2"
                    >
                      {liveClass.course.name}
                    </Link>
                  </p>
                )}
              </div>
              {liveClass.description && (
                <LinkifiedText
                  text={liveClass.description}
                  className="mt-5 block whitespace-pre-line break-words leading-relaxed text-gray-700"
                />
              )}
            </article>
            {host && config?.attendance && <Attendance liveClass={liveClass} />}
          </div>

          {/* Actions first on phones, beside the details on wide screens. */}
          <aside className="order-first space-y-4 lg:order-none">
            <div className={`${CARD} space-y-3 p-5`}>
              {liveClass.status === 'cancelled' ? (
                <p className="text-sm text-gray-500">{t('live_classes.errors.cancelled', { defaultValue: 'This class was cancelled.' })}</p>
              ) : liveClass.status === 'ended' ? (
                <p className="text-sm text-gray-500">{t('live_classes.errors.ended', { defaultValue: 'This class has ended.' })}</p>
              ) : (
                <>
                  <JoinButton liveClass={liveClass} onError={() => query.refetch()} />
                  <p className="text-xs text-gray-400">
                    {liveClass.status === 'live'
                      ? t('live_classes.hint_live', { defaultValue: 'The room is open.' })
                      : host
                        ? liveClass.can_start
                          ? t('live_classes.hint_host_ready', { defaultValue: 'Start the class to open the room for everyone.' })
                          : t('live_classes.hint_host_early', { defaultValue: 'You can open the room shortly before the start time.' })
                        : t('live_classes.hint_learner', { defaultValue: 'You can join once a host opens the room.' })}
                  </p>
                </>
              )}
              {host && liveClass.status === 'live' && (
                <button type="button" onClick={endForEveryone} disabled={ending} className={`${DANGER_BTN} w-full`}>
                  {ending && <Loader2 className="h-4 w-4 animate-spin" />}
                  {t('live_classes.actions.end', { defaultValue: 'End for everyone' })}
                </button>
              )}
            </div>

            {host && liveClass.status !== 'cancelled' && (
              <div className={`${CARD} flex flex-col gap-2 p-5`}>
                <Modal
                  isDialogOpen={editing}
                  onOpenChange={setEditing}
                  minWidth="sm"
                  dialogTitle={t('live_classes.edit_title', { defaultValue: 'Edit class' })}
                  dialogContent={
                    <ScheduleClassForm
                      orgId={liveClass.org_id}
                      existing={liveClass}
                      onCancel={() => setEditing(false)}
                      onDone={() => {
                        setEditing(false)
                        toast.success(t('live_classes.saved', { defaultValue: 'Changes saved' }))
                        refresh()
                      }}
                    />
                  }
                  dialogTrigger={
                    <button type="button" className={SECONDARY_BTN}>
                      <Pencil className="h-4 w-4" />
                      {t('live_classes.actions.edit', { defaultValue: 'Edit' })}
                    </button>
                  }
                />
                <ScopedAction
                  liveClass={liveClass}
                  kind="cancel"
                  onConfirm={async (scope) => {
                    try {
                      await cancelLiveClass(liveClass.class_uuid, scope, token)
                      toast.success(t('live_classes.cancelled_toast', { defaultValue: 'Class cancelled' }))
                      refresh()
                    } catch (e) {
                      toast.error(message(e))
                    }
                  }}
                />
                <ScopedAction
                  liveClass={liveClass}
                  kind="delete"
                  onConfirm={async (scope) => {
                    try {
                      await deleteLiveClass(liveClass.class_uuid, scope, token)
                      toast.success(t('live_classes.deleted_toast', { defaultValue: 'Class deleted' }))
                      refresh()
                      router.push(backHref)
                    } catch (e) {
                      toast.error(message(e))
                    }
                  }}
                />
              </div>
            )}
            {host && liveClass.status === 'cancelled' && (
              <div className={`${CARD} p-5`}>
                <ScopedAction
                  liveClass={liveClass}
                  kind="delete"
                  onConfirm={async (scope) => {
                    try {
                      await deleteLiveClass(liveClass.class_uuid, scope, token)
                      refresh()
                      router.push(backHref)
                    } catch (e) {
                      toast.error(message(e))
                    }
                  }}
                />
              </div>
            )}
          </aside>
        </div>
      </GeneralWrapperStyled>
    </div>
  )
}
