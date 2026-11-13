'use client'
// Fork: schedule (or edit) a live class. Times are entered in the author's
// own time zone; the API stores UTC plus the zone so a series keeps its
// wall-clock time across daylight-saving changes.
import React, { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Loader2 } from 'lucide-react'
import {
  browserTimezone,
  createLiveClasses,
  LiveClassError,
  updateLiveClass,
  type Frequency,
  type LiveClass,
  type Scope,
} from '@services/plugnmeet/liveClasses'
import { PRIMARY_BTN, SECONDARY_BTN, useLiveClassContext } from './shared'

const inputClass =
  'w-full h-9 px-3 text-sm rounded-lg bg-gray-50 border border-gray-200 outline-none focus:border-gray-300 focus:ring-1 focus:ring-gray-200 transition-colors'
const textareaClass =
  'w-full px-3 py-2 text-sm rounded-lg bg-gray-50 border border-gray-200 outline-none focus:border-gray-300 focus:ring-1 focus:ring-gray-200 transition-colors resize-none'
const labelClass = 'text-sm font-medium text-gray-700'

const DURATIONS = [15, 30, 45, 60, 90, 120, 180, 240]

const pad = (n: number) => String(n).padStart(2, '0')

/** `YYYY-MM-DDTHH:mm` for a datetime-local input, in the browser's zone. */
export function toLocalInput(date: Date): string {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`
}

/** The next half hour, at least 15 minutes away. */
export function defaultStart(now = new Date()): Date {
  const d = new Date(now.getTime() + 15 * 60_000)
  d.setSeconds(0, 0)
  d.setMinutes(d.getMinutes() < 30 ? 30 : 60)
  return d
}

interface Props {
  orgId: number
  courseUuid?: string | null
  /** Editing an existing class instead of scheduling a new one. */
  existing?: LiveClass
  onDone: (classes: LiveClass[]) => void
  onCancel: () => void
}

export default function ScheduleClassForm({ orgId, courseUuid, existing, onDone, onCancel }: Props) {
  const { t } = useTranslation()
  const { token } = useLiveClassContext()
  const [title, setTitle] = useState(existing?.title ?? '')
  const [description, setDescription] = useState(existing?.description ?? '')
  const [start, setStart] = useState(() => toLocalInput(existing ? new Date(existing.starts_at) : defaultStart()))
  const [duration, setDuration] = useState(existing?.duration_minutes ?? 60)
  const [frequency, setFrequency] = useState<Frequency>('none')
  const [occurrences, setOccurrences] = useState(4)
  const [scope, setScope] = useState<Scope>('this')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const timezone = useMemo(() => browserTimezone(), [])
  const durations = useMemo(
    () => (DURATIONS.includes(duration) ? DURATIONS : [...DURATIONS, duration].sort((a, b) => a - b)),
    [duration]
  )

  const durationLabel = (minutes: number) =>
    minutes < 60
      ? t('live_classes.form.minutes', { count: minutes, defaultValue: `${minutes} min` })
      : t('live_classes.form.hours', {
          count: minutes / 60,
          defaultValue: `${Number.isInteger(minutes / 60) ? minutes / 60 : (minutes / 60).toFixed(1)} h`,
        })

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    const startsAt = new Date(start)
    if (!title.trim() || Number.isNaN(startsAt.getTime())) {
      setError(t('live_classes.form.invalid', { defaultValue: 'Add a title and a start time.' }))
      return
    }
    setBusy(true)
    try {
      const payload = {
        title: title.trim(),
        description: description.trim(),
        starts_at: startsAt.toISOString(),
        duration_minutes: duration,
        timezone,
      }
      const result = existing
        ? await updateLiveClass(existing.class_uuid, payload, scope, token)
        : await createLiveClasses(
            orgId,
            { ...payload, course_uuid: courseUuid || null, frequency, occurrences: frequency === 'none' ? 1 : occurrences },
            token
          )
      onDone(result)
    } catch (err) {
      const status = err instanceof LiveClassError ? err.status : 0
      setError(
        status === 403
          ? t('live_classes.form.forbidden', { defaultValue: "You can't schedule classes here." })
          : t('live_classes.form.failed', { defaultValue: "The class couldn't be saved. Please try again." })
      )
      setBusy(false)
    }
  }

  return (
    <form onSubmit={submit} className="space-y-5 p-1">
      <div className="space-y-1.5">
        <label htmlFor="lc-title" className={labelClass}>
          {t('live_classes.form.title', { defaultValue: 'Title' })}
        </label>
        <input
          id="lc-title"
          className={inputClass}
          value={title}
          maxLength={200}
          required
          autoFocus
          onChange={(e) => setTitle(e.target.value)}
          placeholder={t('live_classes.form.title_placeholder', { defaultValue: 'e.g. Week 3 review' })}
        />
      </div>

      <div className="space-y-1.5">
        <label htmlFor="lc-description" className={labelClass}>
          {t('live_classes.form.description', { defaultValue: 'Description' })}{' '}
          <span className="font-normal text-gray-400">{t('live_classes.form.optional', { defaultValue: '(optional)' })}</span>
        </label>
        <textarea
          id="lc-description"
          className={textareaClass}
          rows={3}
          maxLength={2000}
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div className="space-y-1.5">
          <label htmlFor="lc-start" className={labelClass}>
            {t('live_classes.form.starts', { defaultValue: 'Starts' })}
          </label>
          <input
            id="lc-start"
            type="datetime-local"
            className={inputClass}
            value={start}
            required
            onChange={(e) => setStart(e.target.value)}
          />
        </div>
        <div className="space-y-1.5">
          <label htmlFor="lc-duration" className={labelClass}>
            {t('live_classes.form.duration', { defaultValue: 'Duration' })}
          </label>
          <select id="lc-duration" className={inputClass} value={duration} onChange={(e) => setDuration(Number(e.target.value))}>
            {durations.map((m) => (
              <option key={m} value={m}>
                {durationLabel(m)}
              </option>
            ))}
          </select>
        </div>
      </div>
      <p className="-mt-3 text-[11px] text-gray-400">
        {t('live_classes.form.timezone_note', { timezone, defaultValue: `Times are in your time zone (${timezone}). Everyone sees them in their own.` })}
      </p>

      {!existing && (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div className="space-y-1.5">
            <label htmlFor="lc-repeat" className={labelClass}>
              {t('live_classes.form.repeat', { defaultValue: 'Repeat' })}
            </label>
            <select id="lc-repeat" className={inputClass} value={frequency} onChange={(e) => setFrequency(e.target.value as Frequency)}>
              <option value="none">{t('live_classes.form.repeat_none', { defaultValue: 'Does not repeat' })}</option>
              <option value="daily">{t('live_classes.form.repeat_daily', { defaultValue: 'Every day' })}</option>
              <option value="weekly">{t('live_classes.form.repeat_weekly', { defaultValue: 'Every week' })}</option>
              <option value="monthly">{t('live_classes.form.repeat_monthly', { defaultValue: 'Every month' })}</option>
            </select>
          </div>
          {frequency !== 'none' && (
            <div className="space-y-1.5">
              <label htmlFor="lc-count" className={labelClass}>
                {t('live_classes.form.occurrences', { defaultValue: 'Number of classes' })}
              </label>
              <input
                id="lc-count"
                type="number"
                min={2}
                max={52}
                className={inputClass}
                value={occurrences}
                onChange={(e) => setOccurrences(Math.max(2, Math.min(52, Number(e.target.value) || 2)))}
              />
            </div>
          )}
        </div>
      )}

      {existing?.series_uuid && (
        <fieldset className="space-y-2">
          <legend className={labelClass}>{t('live_classes.form.apply_to', { defaultValue: 'Apply changes to' })}</legend>
          <div className="flex flex-col gap-2 sm:flex-row sm:gap-4">
            {(['this', 'following'] as Scope[]).map((s) => (
              <label key={s} className="flex items-center gap-2 text-sm text-gray-700">
                <input type="radio" name="lc-scope" value={s} checked={scope === s} onChange={() => setScope(s)} className="accent-black" />
                {s === 'this'
                  ? t('live_classes.scope.this', { defaultValue: 'This class only' })
                  : t('live_classes.scope.following', { defaultValue: 'This and following classes' })}
              </label>
            ))}
          </div>
        </fieldset>
      )}

      {error && (
        <p role="alert" className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">
          {error}
        </p>
      )}

      <div className="flex justify-end gap-2 border-t border-gray-100 pt-4">
        <button type="button" onClick={onCancel} className={SECONDARY_BTN} disabled={busy}>
          {t('live_classes.actions.cancel', { defaultValue: 'Cancel' })}
        </button>
        <button type="submit" className={PRIMARY_BTN} disabled={busy}>
          {busy && <Loader2 className="h-4 w-4 animate-spin" />}
          {existing
            ? t('live_classes.actions.save', { defaultValue: 'Save' })
            : t('live_classes.actions.schedule', { defaultValue: 'Schedule' })}
        </button>
      </div>
    </form>
  )
}
