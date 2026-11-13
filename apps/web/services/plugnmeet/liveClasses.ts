// Fork: live classes on plugNmeet. Client for the API's /plugnmeet routes.
import { getAPIUrl } from '@services/config/config'
import { RequestBodyWithAuthHeader } from '@services/utils/ts/requests'

export type LiveClassStatus = 'scheduled' | 'live' | 'ended' | 'cancelled'
export type Frequency = 'none' | 'daily' | 'weekly' | 'monthly'
export type Scope = 'this' | 'following'

export interface LiveClass {
  class_uuid: string
  org_id: number
  title: string
  description: string
  /** ISO 8601 UTC. */
  starts_at: string
  ends_at: string
  duration_minutes: number
  /** IANA zone the schedule was written in. */
  timezone: string
  series_uuid: string | null
  course: { course_uuid: string; name: string } | null
  status: LiveClassStatus
  can_moderate: boolean
  can_join: boolean
  can_start: boolean
}

export interface LiveClassesConfig {
  enabled: boolean
  quick_room: boolean
  attendance: boolean
}

export interface QuickRoom {
  enabled: boolean
  active: boolean
  can_start: boolean
}

export interface Attendee {
  user_uuid: string
  username: string
  name: string
  first_joined_at: string
  last_left_at: string | null
  total_minutes: number
  in_room: boolean
}

export interface ClassInput {
  title: string
  description: string
  starts_at: string
  duration_minutes: number
  timezone: string
  course_uuid?: string | null
  frequency?: Frequency
  occurrences?: number
}

/** An API failure with the server's machine-readable code when it sent one
 * (NOT_STARTED, ENDED, CANCELLED, TOO_EARLY, UNAVAILABLE, RATE_LIMITED). */
export class LiveClassError extends Error {
  status: number
  code: string | null
  constructor(status: number, code: string | null, message: string) {
    super(message)
    this.status = status
    this.code = code
  }
}

const base = () => `${getAPIUrl()}plugnmeet`

async function call<T>(path: string, method: string, token: string | undefined, body?: unknown): Promise<T> {
  const res = await fetch(`${base()}${path}`, RequestBodyWithAuthHeader(method, body ?? null, null, token))
  if (res.status === 204) return undefined as T
  let data: any = null
  try {
    data = await res.json()
  } catch {
    data = null
  }
  if (!res.ok) {
    const detail = data?.detail
    const code = detail && typeof detail === 'object' ? detail.code ?? null : null
    const message =
      detail && typeof detail === 'object' ? detail.message ?? '' : typeof detail === 'string' ? detail : ''
    throw new LiveClassError(res.status, code, message)
  }
  return data as T
}

let configPromise: Promise<LiveClassesConfig> | null = null

/** Whether this instance has live classes configured. Cached per page load. */
export function getLiveClassesConfig(): Promise<LiveClassesConfig> {
  if (!configPromise) {
    configPromise = call<LiveClassesConfig>('/config', 'GET', undefined).catch(() => {
      configPromise = null
      return { enabled: false, quick_room: false, attendance: false }
    })
  }
  return configPromise
}

export const listLiveClasses = (
  orgId: number,
  when: 'upcoming' | 'past' | 'now',
  token?: string,
  courseUuid?: string,
  limit = 50
) => {
  const params = new URLSearchParams({ when, limit: String(limit) })
  if (courseUuid) params.set('course_uuid', courseUuid)
  return call<LiveClass[]>(`/org/${orgId}/classes?${params}`, 'GET', token)
}

export const getLiveClassPermissions = (orgId: number, token?: string, courseUuid?: string) => {
  const params = courseUuid ? `?${new URLSearchParams({ course_uuid: courseUuid })}` : ''
  return call<{ can_schedule: boolean; can_host_quick_room: boolean }>(`/org/${orgId}/permissions${params}`, 'GET', token)
}

export const createLiveClasses = (orgId: number, input: ClassInput, token?: string) =>
  call<LiveClass[]>(`/org/${orgId}/classes`, 'POST', token, input)

const id = (classUuid: string) => encodeURIComponent(classUuid.replace(/^liveclass_/, ''))

export const getLiveClass = (classUuid: string, token?: string) => call<LiveClass>(`/classes/${id(classUuid)}`, 'GET', token)

export const updateLiveClass = (classUuid: string, input: Partial<ClassInput>, scope: Scope, token?: string) =>
  call<LiveClass[]>(`/classes/${id(classUuid)}?scope=${scope}`, 'PATCH', token, input)

export const cancelLiveClass = (classUuid: string, scope: Scope, token?: string) =>
  call<LiveClass[]>(`/classes/${id(classUuid)}/cancel?scope=${scope}`, 'POST', token)

export const deleteLiveClass = (classUuid: string, scope: Scope, token?: string) =>
  call<void>(`/classes/${id(classUuid)}?scope=${scope}`, 'DELETE', token)

export const endLiveClass = (classUuid: string, token?: string) => call<void>(`/classes/${id(classUuid)}/end`, 'POST', token)

export const getAttendance = (classUuid: string, token?: string) =>
  call<{ enabled: boolean; attendees: Attendee[] }>(`/classes/${id(classUuid)}/attendance`, 'GET', token)

export const getQuickRoom = (orgId: number, token?: string) => call<QuickRoom>(`/org/${orgId}/quick`, 'GET', token)

export const endQuickRoom = (orgId: number, token?: string) => call<void>(`/org/${orgId}/quick/end`, 'POST', token)

/** Only ever follow a join URL that points at the configured meeting
 * client: the token in it is single-use and must not leak elsewhere. */
export function isSafeJoinUrl(url: string): boolean {
  try {
    const u = new URL(url)
    return u.protocol === 'https:' && u.searchParams.has('access_token')
  } catch {
    return false
  }
}

async function openRoom(path: string, token?: string): Promise<void> {
  const { join_url } = await call<{ join_url: string }>(path, 'POST', token)
  if (!isSafeJoinUrl(join_url)) throw new LiveClassError(502, 'UNAVAILABLE', '')
  // Same tab: the token is single-use and short-lived, so it is used at once.
  window.location.assign(join_url)
}

export const joinLiveClass = (classUuid: string, token?: string) => openRoom(`/classes/${id(classUuid)}/join`, token)

export const joinQuickRoom = (orgId: number, token?: string) => openRoom(`/org/${orgId}/quick/join`, token)

/** The browser's IANA zone, for scheduling in the author's local time. */
export function browserTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
  } catch {
    return 'UTC'
  }
}
