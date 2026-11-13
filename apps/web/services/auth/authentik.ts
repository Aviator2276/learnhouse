/**
 * Fork: Authentik (OpenID Connect) sign-in, browser side.
 *
 * The browser never sees an Authentik token or the client secret. It only:
 *  1. asks the API to start a flow (POST /api/auth/authentik/authorize), which
 *     returns the Authentik URL plus a one-time *binding* secret;
 *  2. keeps the binding in sessionStorage (this tab only) and goes to Authentik;
 *  3. on /auth/callback/authentik, posts {code, state, binding} back to the API,
 *     which validates everything and answers like a normal login.
 *
 * Every call goes through the same-origin /api/auth proxy, which refuses
 * cross-site POSTs and turns the response into httpOnly session cookies.
 */

const BINDING_KEY = 'lh_authentik_binding'
const STATE_KEY = 'lh_authentik_state'
// Set after an Authentik sign-in so sign-out can also end the Authentik session.
const SSO_MARKER_KEY = 'lh_authentik_sso'

export interface AuthentikConfig {
  enabled: boolean
  display_name?: string
}

let configPromise: Promise<AuthentikConfig> | null = null

export function getAuthentikConfig(): Promise<AuthentikConfig> {
  if (!configPromise) {
    configPromise = fetch('/api/auth/authentik/config', { credentials: 'same-origin' })
      .then((r) => (r.ok ? r.json() : { enabled: false }))
      .then((data) => ({
        enabled: data?.enabled === true,
        display_name: typeof data?.display_name === 'string' ? data.display_name : 'Authentik',
      }))
      .catch(() => {
        configPromise = null
        return { enabled: false }
      })
  }
  return configPromise
}

function safeSession(): Storage | null {
  try {
    return window.sessionStorage
  } catch {
    return null
  }
}

function safeLocal(): Storage | null {
  try {
    return window.localStorage
  } catch {
    return null
  }
}

/** Only same-site relative paths survive; anything else becomes "/". */
export function safeNextPath(value: string | null | undefined): string {
  if (!value || typeof value !== 'string') return '/'
  if (!value.startsWith('/') || value.startsWith('//') || value.includes('\\')) return '/'
  // eslint-disable-next-line no-control-regex
  if (/[\u0000-\u001f\u007f]/.test(value)) return '/'
  return value
}

/** Allow only the IdP's https (or explicitly http, for LAN testing) URL. */
function safeAuthorizeUrl(url: unknown): string | null {
  if (typeof url !== 'string') return null
  try {
    const u = new URL(url)
    return u.protocol === 'https:' || u.protocol === 'http:' ? u.toString() : null
  } catch {
    return null
  }
}

export async function startAuthentikLogin(options: {
  orgId?: number
  inviteCode?: string
  next?: string
}): Promise<{ ok: false; error: string } | void> {
  const res = await fetch('/api/auth/authentik/authorize', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'same-origin',
    body: JSON.stringify({
      org_id: options.orgId ?? null,
      invite_code: options.inviteCode ?? null,
      next: safeNextPath(options.next),
    }),
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    const detail = data?.detail
    const message = typeof detail === 'string' ? detail : detail?.message
    return { ok: false, error: message || 'Authentik sign-in is unavailable right now.' }
  }
  const url = safeAuthorizeUrl(data?.url)
  const storage = safeSession()
  if (!url || !storage || typeof data.binding !== 'string' || typeof data.state !== 'string') {
    return { ok: false, error: 'Authentik sign-in could not be started.' }
  }
  storage.setItem(BINDING_KEY, data.binding)
  storage.setItem(STATE_KEY, data.state)
  window.location.assign(url)
}

export interface AuthentikCallbackResult {
  user?: any
  tokens?: { access_token: string; expiry?: number }
  mfa_required?: boolean
  mfa_token?: string
  callback_url?: string
}

export async function completeAuthentikLogin(params: URLSearchParams): Promise<AuthentikCallbackResult> {
  const storage = safeSession()
  const binding = storage?.getItem(BINDING_KEY)
  const expectedState = storage?.getItem(STATE_KEY)
  // Single use, whatever happens next.
  storage?.removeItem(BINDING_KEY)
  storage?.removeItem(STATE_KEY)

  const error = params.get('error')
  if (error) {
    throw new Error(error === 'access_denied' ? 'Sign-in was cancelled or denied.' : 'Authentik reported an error.')
  }
  const code = params.get('code')
  const state = params.get('state')
  if (!code || !state) throw new Error('The sign-in response is incomplete.')
  if (!binding || !expectedState || state !== expectedState) {
    // This tab did not start this sign-in (or it already finished).
    throw new Error('This sign-in was not started from this browser tab. Please start again.')
  }

  const body: Record<string, string> = { code, state, binding }
  const iss = params.get('iss')
  if (iss) body.iss = iss

  const res = await fetch('/api/auth/authentik/callback', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify(body),
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    const detail = data?.detail
    const message = typeof detail === 'string' ? detail : detail?.message
    throw new Error(message || 'Authentik sign-in failed.')
  }
  markAuthentikSession()
  return { ...data, callback_url: safeNextPath(data?.callback_url) }
}

export function markAuthentikSession(): void {
  safeLocal()?.setItem(SSO_MARKER_KEY, '1')
}

/**
 * After a local sign-out: when the session came from Authentik, the Authentik
 * end-session URL to continue to (single logout), else null. Clears the marker.
 */
export async function consumeAuthentikLogoutUrl(): Promise<string | null> {
  const local = safeLocal()
  if (local?.getItem(SSO_MARKER_KEY) !== '1') return null
  local.removeItem(SSO_MARKER_KEY)
  try {
    const res = await fetch('/api/auth/authentik/logout-url', {
      credentials: 'same-origin',
      signal: AbortSignal.timeout(3000),
    })
    if (!res.ok) return null
    const data = await res.json()
    return safeAuthorizeUrl(data?.url)
  } catch {
    return null
  }
}
