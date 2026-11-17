'use client'
// Fork: /dash/email-notifications. Organization admins and maintainers set
// the defaults, the digest schedule and assignment emails, and send
// themselves a test email.
import React, { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import toast from 'react-hot-toast'
import { BookOpen, ChatsCircle, EnvelopeSimple, PaperPlaneTilt, SpinnerGap } from '@phosphor-icons/react'
import { Breadcrumbs } from '@components/Objects/Breadcrumbs/Breadcrumbs'
import { Switch } from '@components/ui/switch'
import { useLHSession } from '@components/Contexts/LHSessionContext'
import { useOrg } from '@components/Contexts/OrgContext'
import {
  EmailNotificationsError,
  LEVELS,
  REMINDER_DAYS,
  getOrgSettings,
  saveOrgSettings,
  sendTestEmail,
  type Level,
  type OrgSettings,
  type OrgSettingsInput,
  type ReminderDays,
  type TestKind,
} from '@services/notifications/emailNotifications'
import { reminderLabel } from './EmailNotificationsDialog'
import { Row, Segmented, hourLabel, useLevelText, weekdayName } from './shared'

const SELECT = 'rounded-lg border border-gray-200 bg-white px-2.5 py-1.5 text-sm text-gray-800 max-w-[11rem] sm:max-w-none'
const CARD = 'rounded-xl bg-white nice-shadow px-5 py-4'

function timeZones(current: string): string[] {
  let zones: string[] = []
  try {
    zones = (Intl as any).supportedValuesOf('timeZone') as string[]
  } catch {
    zones = []
  }
  return Array.from(new Set(['UTC', current, ...zones])).filter(Boolean)
}

function Card({ icon, title, children }: { icon: React.ReactNode; title: string; children: React.ReactNode }) {
  return (
    <section className={CARD}>
      <h2 className="flex items-center gap-2 text-sm font-semibold text-gray-900 mb-1 [&>svg]:text-gray-400">
        {icon}
        {title}
      </h2>
      <div className="divide-y divide-gray-100">{children}</div>
    </section>
  )
}

export default function EmailNotificationSettings() {
  const { t, i18n } = useTranslation()
  const org = useOrg() as any
  const session = useLHSession() as any
  const token = session?.data?.tokens?.access_token as string | undefined
  const orgId = typeof org?.id === 'number' ? (org.id as number) : 0
  const levelText = useLevelText()

  const [settings, setSettings] = useState<OrgSettings | null>(null)
  const [form, setForm] = useState<OrgSettingsInput | null>(null)
  const [error, setError] = useState<'forbidden' | 'failed' | null>(null)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState<TestKind | null>(null)

  useEffect(() => {
    if (!orgId || !token) return
    let cancelled = false
    getOrgSettings(orgId, token)
      .then((data) => {
        if (cancelled) return
        setSettings(data)
        const { instance_enabled: _i, last_digest_at: _l, delay_minutes: _d, ...input } = data
        setForm(input)
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof EmailNotificationsError && e.status === 403 ? 'forbidden' : 'failed')
      })
    return () => {
      cancelled = true
    }
  }, [orgId, token])

  const zones = useMemo(() => timeZones(form?.timezone || 'UTC'), [form?.timezone])
  const dirty = useMemo(() => {
    if (!settings || !form) return false
    return (Object.keys(form) as (keyof OrgSettingsInput)[]).some((key) => form[key] !== settings[key])
  }, [settings, form])

  const set = <K extends keyof OrgSettingsInput>(key: K, value: OrgSettingsInput[K]) =>
    setForm((current) => (current ? { ...current, [key]: value } : current))

  const save = async () => {
    if (!form) return
    setSaving(true)
    try {
      const data = await saveOrgSettings(orgId, form, token)
      setSettings(data)
      toast.success(t('email_notifications.admin.saved', { defaultValue: 'Settings saved' }))
    } catch {
      toast.error(t('email_notifications.save_failed', { defaultValue: 'Could not save. Try again.' }))
    } finally {
      setSaving(false)
    }
  }

  const test = async (kind: TestKind) => {
    setTesting(kind)
    try {
      const { sent_to } = await sendTestEmail(orgId, kind, token)
      toast.success(t('email_notifications.admin.test_sent', { defaultValue: 'Test email sent to {{email}}', email: sent_to }))
    } catch (e) {
      const message = e instanceof EmailNotificationsError ? e.message : ''
      toast.error(message || t('email_notifications.admin.test_failed', { defaultValue: 'The test email could not be sent.' }))
    } finally {
      setTesting(null)
    }
  }

  const title = t('email_notifications.title', { defaultValue: 'Email notifications' })

  return (
    <div className="h-full w-full bg-[#f8f8f8] ps-4 pe-4 sm:ps-10 sm:pe-10 pb-24">
      <div className="mb-6 pt-6">
        <Breadcrumbs items={[{ label: title, href: '/dash/email-notifications', icon: <EnvelopeSimple size={14} /> }]} />
        <h1 className="text-3xl font-bold mt-4">{title}</h1>
        <p className="text-sm text-gray-500 mt-1 max-w-2xl">
          {t('email_notifications.admin.description', {
            defaultValue: 'What members are emailed about communities and assignments. Each member can change their own choices from the profile menu.',
          })}
        </p>
      </div>

      {error && (
        <p className={`${CARD} text-sm text-gray-600 max-w-2xl`}>
          {error === 'forbidden'
            ? t('email_notifications.admin.forbidden', { defaultValue: 'Only organization admins and maintainers can change these settings.' })
            : t('email_notifications.admin.load_failed', { defaultValue: 'Could not load the settings. Reload the page to try again.' })}
        </p>
      )}

      {!error && (!settings || !form) && <SpinnerGap className="animate-spin text-gray-400" />}

      {settings && form && (
        <div className="grid gap-4 max-w-2xl">
          {!settings.instance_enabled && (
            <p className="rounded-lg bg-amber-50 px-3 py-2.5 text-sm text-amber-800 ring-1 ring-amber-200">
              {t('email_notifications.admin.instance_off', {
                defaultValue: 'Notification emails are switched off on this server (LEARNHOUSE_NOTIFICATION_EMAILS_DISABLED). Nothing is sent until that is removed.',
              })}
            </p>
          )}

          <section className={CARD}>
            <Row
              title={t('email_notifications.admin.enabled', { defaultValue: 'Send notification emails' })}
              hint={t('email_notifications.admin.enabled_hint', {
                defaultValue: 'Turn off to stop every community and assignment email for this organization.',
              })}
            >
              <Switch
                checked={form.enabled}
                onCheckedChange={(on) => set('enabled', on)}
                aria-label={t('email_notifications.admin.enabled', { defaultValue: 'Send notification emails' })}
              />
            </Row>
          </section>

          <Card icon={<ChatsCircle size={15} />} title={t('email_notifications.communities', { defaultValue: 'Communities' })}>
            <Row
              title={t('email_notifications.admin.default_level', { defaultValue: 'Default for members' })}
              hint={levelText[form.default_level].hint}
            >
              <select
                className={SELECT}
                value={form.default_level}
                onChange={(e) => set('default_level', e.target.value as Level)}
                aria-label={t('email_notifications.admin.default_level', { defaultValue: 'Default for members' })}
              >
                {LEVELS.map((level) => (
                  <option key={level} value={level}>
                    {levelText[level].label}
                  </option>
                ))}
              </select>
            </Row>
            <Row
              title={t('email_notifications.digest', { defaultValue: 'Weekly digest' })}
              hint={t('email_notifications.admin.digest_hint', {
                defaultValue: 'The most active discussions of the past week. Skipped for a member when there is nothing new they can read.',
              })}
            >
              <Switch
                checked={form.digest_enabled}
                onCheckedChange={(on) => set('digest_enabled', on)}
                aria-label={t('email_notifications.digest', { defaultValue: 'Weekly digest' })}
              />
            </Row>
            {form.digest_enabled && (
              <Row title={t('email_notifications.admin.digest_when', { defaultValue: 'Sent on' })}>
                <div className="flex flex-wrap justify-end gap-2">
                  <select
                    className={SELECT}
                    value={form.digest_weekday}
                    onChange={(e) => set('digest_weekday', Number(e.target.value))}
                    aria-label={t('email_notifications.admin.digest_day', { defaultValue: 'Day' })}
                  >
                    {[0, 1, 2, 3, 4, 5, 6].map((day) => (
                      <option key={day} value={day}>
                        {weekdayName(day, i18n.language)}
                      </option>
                    ))}
                  </select>
                  <select
                    className={SELECT}
                    value={form.digest_hour}
                    onChange={(e) => set('digest_hour', Number(e.target.value))}
                    aria-label={t('email_notifications.admin.digest_hour', { defaultValue: 'Time' })}
                  >
                    {Array.from({ length: 24 }, (_, hour) => (
                      <option key={hour} value={hour}>
                        {hourLabel(hour)}
                      </option>
                    ))}
                  </select>
                </div>
              </Row>
            )}
            <Row
              title={t('email_notifications.admin.timezone', { defaultValue: 'Time zone' })}
              hint={t('email_notifications.admin.timezone_hint', { defaultValue: 'Used for the digest and for due times in emails.' })}
            >
              <select
                className={SELECT}
                value={form.timezone}
                onChange={(e) => set('timezone', e.target.value)}
                aria-label={t('email_notifications.admin.timezone', { defaultValue: 'Time zone' })}
              >
                {zones.map((zone) => (
                  <option key={zone} value={zone}>
                    {zone.replace(/_/g, ' ')}
                  </option>
                ))}
              </select>
            </Row>
            <p className="pt-2.5 text-xs text-gray-500">
              {t('email_notifications.admin.delay', {
                defaultValue: 'New posts are emailed about {{count}} minutes after they are written, so authors can still fix them.',
                count: settings.delay_minutes,
              })}{' '}
              {settings.last_digest_at
                ? t('email_notifications.admin.last_digest', {
                    defaultValue: 'Last digest: {{when}}.',
                    when: new Date(settings.last_digest_at).toLocaleString(i18n.language),
                  })
                : t('email_notifications.admin.no_digest_yet', { defaultValue: 'No digest has been sent yet.' })}
            </p>
          </Card>

          <Card icon={<BookOpen size={15} />} title={t('email_notifications.assignments', { defaultValue: 'Assignments' })}>
            <Row
              title={t('email_notifications.admin.assignments_enabled', { defaultValue: 'Assignment emails' })}
              hint={t('email_notifications.admin.assignments_hint', {
                defaultValue: 'New assignments, due dates that are set or moved, and reminders, for learners enrolled in the course.',
              })}
            >
              <Switch
                checked={form.assignments_enabled}
                onCheckedChange={(on) => set('assignments_enabled', on)}
                aria-label={t('email_notifications.admin.assignments_enabled', { defaultValue: 'Assignment emails' })}
              />
            </Row>
            {form.assignments_enabled && (
              <Row
                title={t('email_notifications.admin.reminder_default', { defaultValue: 'Default reminder' })}
                hint={t('email_notifications.admin.reminder_hint', { defaultValue: "Only sent to learners who haven't submitted." })}
              >
                <Segmented<ReminderDays>
                  value={form.assignment_reminder_days}
                  options={REMINDER_DAYS.map((days) => ({ value: days, label: reminderLabel(days, t) }))}
                  onChange={(days) => set('assignment_reminder_days', days)}
                />
              </Row>
            )}
          </Card>

          <Card icon={<PaperPlaneTilt size={15} />} title={t('email_notifications.admin.test', { defaultValue: 'Send a test email' })}>
            <div className="py-2.5">
              <p className="text-xs text-gray-500">
                {t('email_notifications.admin.test_hint', {
                  defaultValue: 'Sent to your own address only. Uses this week\'s discussions when there are some, otherwise a sample.',
                })}
              </p>
              <div className="flex flex-wrap gap-2 mt-3">
                {(
                  [
                    ['instant', t('email_notifications.admin.test_instant', { defaultValue: 'New post' })],
                    ['digest', t('email_notifications.digest', { defaultValue: 'Weekly digest' })],
                    ['assignment', t('email_notifications.admin.test_assignment', { defaultValue: 'Assignment reminder' })],
                  ] as [TestKind, string][]
                ).map(([kind, label]) => (
                  <button
                    key={kind}
                    type="button"
                    disabled={testing !== null}
                    onClick={() => test(kind)}
                    className="rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-xs font-medium text-gray-800 hover:bg-gray-50 disabled:opacity-50 flex items-center gap-1.5"
                  >
                    {testing === kind && <SpinnerGap size={12} className="animate-spin" />}
                    {label}
                  </button>
                ))}
              </div>
            </div>
          </Card>

          <div className="flex justify-end">
            <button
              type="button"
              onClick={save}
              disabled={!dirty || saving}
              className="rounded-lg bg-black px-5 py-2 text-xs font-bold text-white nice-shadow disabled:opacity-40 flex items-center gap-2"
            >
              {saving && <SpinnerGap size={12} className="animate-spin" />}
              {t('email_notifications.admin.save', { defaultValue: 'Save changes' })}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
