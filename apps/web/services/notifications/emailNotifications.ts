// Fork: email notifications for communities and assignments. Client for the
// API's /email-notifications routes.
import { getAPIUrl } from '@services/config/config'
import { RequestBodyWithAuthHeader } from '@services/utils/ts/requests'

export type Level = 'off' | 'mentions' | 'replies' | 'highlights' | 'all'
export type ReminderDays = 1 | 2 | 3 | 7
export type TestKind = 'instant' | 'digest' | 'assignment'

export const LEVELS: Level[] = ['highlights', 'all', 'replies', 'mentions', 'off']
export const REMINDER_DAYS: ReminderDays[] = [1, 2, 3, 7]

export interface CommunityChoice {
  community_uuid: string
  name: string
  muted: boolean
}

export interface LabelChoice {
  id: string
  name: string
  muted: boolean
}

export interface Preferences {
  /** False when notification email is off for the instance or the organization. */
  available: boolean
  level: Level
  uses_default: boolean
  default_level: Level
  weekly_digest: boolean
  digest_available: boolean
  /** 0 = Monday. */
  digest_weekday: number
  digest_hour: number
  timezone: string
  communities: CommunityChoice[]
  labels: LabelChoice[]
  assignments_available: boolean
  assignment_updates: boolean
  assignment_reminders: boolean
  assignment_reminder_days: ReminderDays
  can_manage: boolean
}

export interface PreferencesInput {
  level: Level
  weekly_digest: boolean
  muted_community_uuids: string[]
  muted_labels: string[]
  assignment_updates: boolean
  assignment_reminders: boolean
  assignment_reminder_days: ReminderDays | null
}

export interface OrgSettings {
  instance_enabled: boolean
  enabled: boolean
  default_level: Level
  digest_enabled: boolean
  digest_weekday: number
  digest_hour: number
  timezone: string
  assignments_enabled: boolean
  assignment_reminder_days: ReminderDays
  last_digest_at: string | null
  delay_minutes: number
}

export type OrgSettingsInput = Omit<OrgSettings, 'instance_enabled' | 'last_digest_at' | 'delay_minutes'>

export class EmailNotificationsError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

const base = () => `${getAPIUrl()}email-notifications`

async function call<T>(path: string, method: string, token: string | undefined, body?: unknown): Promise<T> {
  const res = await fetch(`${base()}${path}`, RequestBodyWithAuthHeader(method, body ?? null, null, token))
  let data: any = null
  try {
    data = await res.json()
  } catch {
    data = null
  }
  if (!res.ok) {
    const detail = data?.detail
    const message =
      detail && typeof detail === 'object' && !Array.isArray(detail)
        ? detail.message ?? ''
        : typeof detail === 'string'
          ? detail
          : ''
    throw new EmailNotificationsError(res.status, message)
  }
  return data as T
}

/** The body the API expects, built from what it last returned. */
export function toInput(prefs: Preferences): PreferencesInput {
  return {
    level: prefs.level,
    weekly_digest: prefs.weekly_digest,
    muted_community_uuids: prefs.communities.filter((c) => c.muted).map((c) => c.community_uuid),
    muted_labels: prefs.labels.filter((l) => l.muted).map((l) => l.id),
    assignment_updates: prefs.assignment_updates,
    assignment_reminders: prefs.assignment_reminders,
    assignment_reminder_days: prefs.assignment_reminder_days,
  }
}

export const getPreferences = (orgId: number, token?: string) =>
  call<Preferences>(`/org/${orgId}/preferences`, 'GET', token)

export const savePreferences = (orgId: number, input: PreferencesInput, token?: string) =>
  call<Preferences>(`/org/${orgId}/preferences`, 'PUT', token, input)

export const getOrgSettings = (orgId: number, token?: string) =>
  call<OrgSettings>(`/org/${orgId}/settings`, 'GET', token)

export const saveOrgSettings = (orgId: number, input: OrgSettingsInput, token?: string) =>
  call<OrgSettings>(`/org/${orgId}/settings`, 'PUT', token, input)

export const sendTestEmail = (orgId: number, kind: TestKind, token?: string) =>
  call<{ sent_to: string }>(`/org/${orgId}/test`, 'POST', token, { kind })

/** The browser event that opens the settings dialog from anywhere. */
export const OPEN_EMAIL_NOTIFICATIONS_EVENT = 'lh:open-email-notifications'

export function openEmailNotifications(): void {
  if (typeof window !== 'undefined') window.dispatchEvent(new Event(OPEN_EMAIL_NOTIFICATIONS_EVENT))
}
