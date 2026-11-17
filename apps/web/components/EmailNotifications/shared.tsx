'use client'
// Fork: pieces shared by the email notification dialog and the organization
// settings page. Same look as the Messages settings dialog.
import React from 'react'
import { useTranslation } from 'react-i18next'
import type { Level } from '@services/notifications/emailNotifications'

export function Row({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4 py-2.5">
      <div className="min-w-0">
        <p className="text-sm font-medium text-gray-800">{title}</p>
        {hint && <p className="text-xs text-gray-500 mt-0.5">{hint}</p>}
      </div>
      <div className="flex-shrink-0">{children}</div>
    </div>
  )
}

export function Section({ icon, title, children }: { icon: React.ReactNode; title: string; children: React.ReactNode }) {
  return (
    <section className="py-4 border-t border-gray-100 first:border-t-0 first:pt-0">
      <h3 className="flex items-center gap-2 text-sm font-semibold text-gray-900 mb-1 [&>svg]:text-gray-400">
        {icon}
        {title}
      </h3>
      {children}
    </section>
  )
}

export function Segmented<T extends string | number>({
  value,
  options,
  onChange,
}: {
  value: T
  options: { value: T; label: string }[]
  onChange: (_v: T) => void
}) {
  return (
    <div className="inline-flex flex-wrap rounded-lg bg-gray-100 p-0.5">
      {options.map((o) => (
        <button
          key={String(o.value)}
          type="button"
          aria-pressed={value === o.value}
          onClick={() => onChange(o.value)}
          className={`px-2.5 py-1 text-xs font-medium rounded-md transition-colors ${
            value === o.value ? 'bg-white shadow-sm text-gray-900' : 'text-gray-500 hover:text-gray-800'
          }`}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}

/** Name and one-line explanation of each community email level. */
export function useLevelText(): Record<Level, { label: string; hint: string }> {
  const { t } = useTranslation()
  return {
    highlights: {
      label: t('email_notifications.level_highlights', { defaultValue: 'Highlights' }),
      hint: t('email_notifications.level_highlights_hint', {
        defaultValue: 'Posts from instructors, mentions, and replies in discussions you started or joined.',
      }),
    },
    all: {
      label: t('email_notifications.level_all', { defaultValue: 'Everything' }),
      hint: t('email_notifications.level_all_hint', { defaultValue: 'Every new post and comment in your communities.' }),
    },
    replies: {
      label: t('email_notifications.level_replies', { defaultValue: 'Mentions and replies' }),
      hint: t('email_notifications.level_replies_hint', {
        defaultValue: 'When someone mentions you, or replies in a discussion you started or joined.',
      }),
    },
    mentions: {
      label: t('email_notifications.level_mentions', { defaultValue: 'Mentions only' }),
      hint: t('email_notifications.level_mentions_hint', { defaultValue: 'Only when someone writes @ and your username.' }),
    },
    off: {
      label: t('email_notifications.level_off', { defaultValue: 'Off' }),
      hint: t('email_notifications.level_off_hint', { defaultValue: 'No community emails and no weekly digest.' }),
    },
  }
}

/** Weekday name in the reader's language. 0 = Monday, like the API. */
export function weekdayName(weekday: number, locale?: string): string {
  // 2024-01-01 was a Monday.
  const date = new Date(Date.UTC(2024, 0, 1 + (((weekday % 7) + 7) % 7)))
  return new Intl.DateTimeFormat(locale, { weekday: 'long', timeZone: 'UTC' }).format(date)
}

/** "08:00" style label for an hour of the day. */
export function hourLabel(hour: number): string {
  return `${String(hour).padStart(2, '0')}:00`
}
