'use client'
// Fork: the "Email notifications" dialog. Opened from the profile menu; on a
// phone the shared Dialog shows it as a bottom sheet.
import React, { useCallback, useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import { useTranslation } from 'react-i18next'
import toast from 'react-hot-toast'
import { BookOpen, ChatsCircle, Check, GearSix, SpinnerGap } from '@phosphor-icons/react'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@components/ui/dialog'
import { Switch } from '@components/ui/switch'
import { useLHSession } from '@components/Contexts/LHSessionContext'
import { useOrg } from '@components/Contexts/OrgContext'
import { getUriWithOrg } from '@services/config/config'
import {
  LEVELS,
  REMINDER_DAYS,
  getPreferences,
  savePreferences,
  toInput,
  type Level,
  type Preferences,
  type ReminderDays,
} from '@services/notifications/emailNotifications'
import { Row, Section, Segmented, useLevelText, weekdayName } from './shared'

export default function EmailNotificationsDialog({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (_open: boolean) => void
}) {
  const { t } = useTranslation()
  const org = useOrg() as any
  const session = useLHSession() as any
  const token = session?.data?.tokens?.access_token as string | undefined
  const orgId = typeof org?.id === 'number' ? (org.id as number) : 0
  const levelText = useLevelText()

  const [prefs, setPrefs] = useState<Preferences | null>(null)
  const [failed, setFailed] = useState(false)
  const [saved, setSaved] = useState(false)
  // Saves run one after another so a slow request cannot overwrite a newer choice.
  const queue = useRef<Promise<void>>(Promise.resolve())
  const savedTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    if (!open || !orgId || !token) return
    let cancelled = false
    getPreferences(orgId, token)
      .then((data) => {
        if (cancelled) return
        setFailed(false)
        setPrefs(data)
      })
      .catch(() => {
        if (!cancelled) setFailed(true)
      })
    return () => {
      cancelled = true
    }
  }, [open, orgId, token])

  useEffect(() => () => {
    if (savedTimer.current) clearTimeout(savedTimer.current)
  }, [])

  const update = useCallback(
    (change: (_current: Preferences) => Preferences) => {
      setPrefs((current) => {
        if (!current) return current
        const next = change(current)
        queue.current = queue.current.then(async () => {
          try {
            const fresh = await savePreferences(orgId, toInput(next), token)
            setPrefs(fresh)
            setSaved(true)
            if (savedTimer.current) clearTimeout(savedTimer.current)
            savedTimer.current = setTimeout(() => setSaved(false), 2000)
          } catch {
            toast.error(t('email_notifications.save_failed', { defaultValue: 'Could not save. Try again.' }))
            setPrefs(current)
          }
        })
        return next
      })
    },
    [orgId, token, t]
  )

  const title = t('email_notifications.title', { defaultValue: 'Email notifications' })

  if (!prefs) {
    return (
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="max-w-lg p-10 flex flex-col items-center gap-3">
          <DialogTitle className="sr-only">{title}</DialogTitle>
          {failed ? (
            <p className="text-sm text-gray-500 text-center">
              {t('email_notifications.load_failed', { defaultValue: 'Could not load your settings. Close this and try again.' })}
            </p>
          ) : (
            <SpinnerGap className="animate-spin text-gray-400" />
          )}
        </DialogContent>
      </Dialog>
    )
  }

  const off = prefs.level === 'off'
  const digestDay = weekdayName(prefs.digest_weekday)

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg p-0 overflow-hidden" onOpenAutoFocus={(e) => e.preventDefault()}>
        <DialogHeader className="ps-5 pe-14 py-4 border-b border-gray-100 space-y-0 text-start">
          <DialogTitle className="text-xl font-semibold text-gray-900">{title}</DialogTitle>
          <DialogDescription className="text-sm text-gray-500">
            {t('email_notifications.description', {
              defaultValue: 'Choose which emails you get from {{org}}.',
              org: org?.name || t('email_notifications.this_org', { defaultValue: 'this organization' }),
            })}
          </DialogDescription>
        </DialogHeader>

        <div className="px-5 pt-4 pb-5 max-h-[70vh] overflow-y-auto" data-testid="email-notifications-body">
          {!prefs.available && (
            <p className="mb-4 rounded-lg bg-amber-50 px-3 py-2.5 text-sm text-amber-800 ring-1 ring-amber-200">
              {t('email_notifications.unavailable', {
                defaultValue: 'Email notifications are turned off for this organization. Your choices are saved for when they are turned on.',
              })}
            </p>
          )}

          <Section icon={<ChatsCircle size={15} />} title={t('email_notifications.communities', { defaultValue: 'Communities' })}>
            <div className="grid gap-2 mt-2" role="radiogroup" aria-label={t('email_notifications.communities', { defaultValue: 'Communities' })}>
              {LEVELS.map((level: Level) => {
                const selected = prefs.level === level
                return (
                  <button
                    key={level}
                    type="button"
                    role="radio"
                    aria-checked={selected}
                    onClick={() => update((p) => ({ ...p, level, uses_default: false }))}
                    className={`text-start rounded-lg border px-3 py-2.5 transition-colors ${
                      selected ? 'border-gray-900 bg-gray-50' : 'border-gray-200 hover:bg-gray-50'
                    }`}
                  >
                    <span className="flex items-center justify-between gap-2">
                      <span className="text-sm font-medium text-gray-900 flex items-center gap-2">
                        {levelText[level].label}
                        {prefs.default_level === level && (
                          <span className="rounded-full bg-gray-100 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-gray-500">
                            {t('email_notifications.default', { defaultValue: 'Default' })}
                          </span>
                        )}
                      </span>
                      {selected && <Check size={14} weight="bold" className="text-gray-900 shrink-0" />}
                    </span>
                    <span className="block text-xs text-gray-500 mt-0.5">{levelText[level].hint}</span>
                  </button>
                )
              })}
            </div>

            {prefs.digest_available && (
              <Row
                title={t('email_notifications.digest', { defaultValue: 'Weekly digest' })}
                hint={t('email_notifications.digest_hint', {
                  defaultValue: 'The most active discussions of the week, every {{day}}.',
                  day: digestDay,
                })}
              >
                <Switch
                  checked={prefs.weekly_digest && !off}
                  disabled={off}
                  onCheckedChange={(on) => update((p) => ({ ...p, weekly_digest: on }))}
                  aria-label={t('email_notifications.digest', { defaultValue: 'Weekly digest' })}
                />
              </Row>
            )}

            {!off && prefs.communities.length > 0 && (
              <div className="mt-3">
                <p className="text-sm font-medium text-gray-800">
                  {t('email_notifications.which_communities', { defaultValue: 'Communities to follow' })}
                </p>
                <p className="text-xs text-gray-500 mt-0.5">
                  {t('email_notifications.which_communities_hint', {
                    defaultValue: 'Turn one off to stop every email about it, mentions included.',
                  })}
                </p>
                <div className="mt-2 rounded-lg border border-gray-200 divide-y divide-gray-100 max-h-44 overflow-y-auto">
                  {prefs.communities.map((community) => (
                    <label key={community.community_uuid} className="flex items-center justify-between gap-3 px-3 py-2 cursor-pointer">
                      <span className="text-sm text-gray-800 truncate">{community.name}</span>
                      <Switch
                        checked={!community.muted}
                        onCheckedChange={(on) =>
                          update((p) => ({
                            ...p,
                            communities: p.communities.map((c) =>
                              c.community_uuid === community.community_uuid ? { ...c, muted: !on } : c
                            ),
                          }))
                        }
                        aria-label={community.name}
                      />
                    </label>
                  ))}
                </div>
              </div>
            )}

            {!off && (
              <div className="mt-3">
                <p className="text-sm font-medium text-gray-800">
                  {t('email_notifications.post_types', { defaultValue: 'Post types' })}
                </p>
                <p className="text-xs text-gray-500 mt-0.5">
                  {t('email_notifications.post_types_hint', {
                    defaultValue: 'Applies to new posts and the digest. Mentions and replies still reach you.',
                  })}
                </p>
                <div className="flex flex-wrap gap-1.5 mt-2">
                  {prefs.labels.map((label) => (
                    <button
                      key={label.id}
                      type="button"
                      aria-pressed={!label.muted}
                      onClick={() =>
                        update((p) => ({
                          ...p,
                          labels: p.labels.map((l) => (l.id === label.id ? { ...l, muted: !l.muted } : l)),
                        }))
                      }
                      className={`rounded-md border px-2.5 py-1 text-xs font-medium transition-colors ${
                        label.muted
                          ? 'border-gray-200 text-gray-400 line-through hover:bg-gray-50'
                          : 'border-gray-900 bg-gray-900 text-white'
                      }`}
                    >
                      {t(`email_notifications.label_${label.id}`, { defaultValue: label.name })}
                    </button>
                  ))}
                </div>
              </div>
            )}
          </Section>

          <Section icon={<BookOpen size={15} />} title={t('email_notifications.assignments', { defaultValue: 'Assignments' })}>
            {!prefs.assignments_available && prefs.available && (
              <p className="text-xs text-gray-500 mt-1">
                {t('email_notifications.assignments_unavailable', {
                  defaultValue: 'Assignment emails are turned off for this organization.',
                })}
              </p>
            )}
            <Row
              title={t('email_notifications.assignment_updates', { defaultValue: 'New assignments and due dates' })}
              hint={t('email_notifications.assignment_updates_hint', {
                defaultValue: 'When an assignment is published or its due date is set or moved, in courses you are enrolled in.',
              })}
            >
              <Switch
                checked={prefs.assignment_updates}
                onCheckedChange={(on) => update((p) => ({ ...p, assignment_updates: on }))}
                aria-label={t('email_notifications.assignment_updates', { defaultValue: 'New assignments and due dates' })}
              />
            </Row>
            <Row
              title={t('email_notifications.assignment_reminders', { defaultValue: 'Due date reminders' })}
              hint={t('email_notifications.assignment_reminders_hint', {
                defaultValue: "Before an assignment is due, if you haven't submitted it.",
              })}
            >
              <Switch
                checked={prefs.assignment_reminders}
                onCheckedChange={(on) => update((p) => ({ ...p, assignment_reminders: on }))}
                aria-label={t('email_notifications.assignment_reminders', { defaultValue: 'Due date reminders' })}
              />
            </Row>
            {prefs.assignment_reminders && (
              <div className="flex flex-col gap-2 py-2.5 sm:flex-row sm:items-center sm:justify-between">
                <p className="text-sm font-medium text-gray-800">
                  {t('email_notifications.remind_me', { defaultValue: 'Remind me' })}
                </p>
                <Segmented<ReminderDays>
                  value={prefs.assignment_reminder_days}
                  options={REMINDER_DAYS.map((days) => ({ value: days, label: reminderLabel(days, t) }))}
                  onChange={(days) => update((p) => ({ ...p, assignment_reminder_days: days }))}
                />
              </div>
            )}
          </Section>
        </div>

        <div className="flex items-center justify-between gap-3 px-5 py-3 border-t border-gray-100 bg-gray-50/60">
          <span className="text-xs text-gray-500 flex items-center gap-1.5" aria-live="polite">
            {saved ? (
              <>
                <Check size={12} weight="bold" />
                {t('email_notifications.saved', { defaultValue: 'Saved' })}
              </>
            ) : (
              t('email_notifications.autosave', { defaultValue: 'Changes save as you make them.' })
            )}
          </span>
          {prefs.can_manage && (
            <Link
              href={getUriWithOrg(org?.slug, '/dash/email-notifications')}
              onClick={() => onOpenChange(false)}
              className="text-xs font-medium text-gray-700 hover:text-gray-900 flex items-center gap-1.5"
            >
              <GearSix size={14} />
              {t('email_notifications.org_settings', { defaultValue: 'Organization settings' })}
            </Link>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}

export function reminderLabel(days: ReminderDays, t: (_key: string, _options?: any) => string): string {
  if (days === 7) return t('email_notifications.week_before', { defaultValue: '1 week before' })
  if (days === 1) return t('email_notifications.day_before', { defaultValue: '1 day before' })
  return t('email_notifications.days_before', { defaultValue: '{{count}} days before', count: days })
}
