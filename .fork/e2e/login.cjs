// Browser end-to-end: LearnHouse login page -> real Authentik -> back signed in.
// Usage: node login.cjs <LH_IP> <AK_IP> <screenshot dir> [save signed-in state to]
const { chromium } = require('playwright')

const [lhIp, akIp, outDir = '.', statePath] = process.argv.slice(2)
const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a)

async function main() {
const browser = await chromium.launch({
  args: ['--no-proxy-server', `--host-resolver-rules=MAP learn.localhost ${lhIp}, MAP auth.localhost ${akIp}`],
})
const page = await (await browser.newContext()).newPage()
try {
  await page.goto('http://learn.localhost/login', { waitUntil: 'domcontentloaded' })
  const btn = page.getByRole('button', { name: /Sign in with Authentik/i })
  await btn.waitFor({ timeout: 90000 })
  log('login page offers Authentik')
  await btn.click()
  await page.waitForURL((u) => u.hostname === 'auth.localhost', { timeout: 30000 })
  log('at Authentik', new URL(page.url()).pathname)

  // Authenticate through Authentik's flow executor API from its own page
  // (deterministic; Authentik's UI is not what is under test here).
  await page.waitForLoadState('networkidle').catch(() => {})
  const done = await page.evaluate(async () => {
    const exec = '/api/v3/flows/executor/default-authentication-flow/?query=' + encodeURIComponent(location.search.slice(1))
    const csrf = (document.cookie.match(/authentik_csrf=([^;]+)/) || [])[1]
    const h = { 'Content-Type': 'application/json', ...(csrf ? { 'X-authentik-CSRF': csrf } : {}) }
    const post = async (body) => (await fetch(exec, { method: 'POST', headers: h, credentials: 'include', body: JSON.stringify(body) })).json()
    await fetch(exec, { credentials: 'include' })
    let r = await post({ component: 'ak-stage-identification', uid_field: 'alice' })
    if (r.component === 'ak-stage-password') r = await post({ component: 'ak-stage-password', password: 'Alice-Passw0rd!' })
    return r
  })
  if (done.component !== 'xak-flow-redirect') throw new Error('Authentik login did not finish: ' + JSON.stringify(done).slice(0, 300))
  await page.goto(new URL(done.to, 'http://auth.localhost:9000').toString())

  await page.waitForURL((u) => u.hostname === 'learn.localhost' && !u.pathname.startsWith('/auth/callback'), { timeout: 90000 })
  log('back on LearnHouse', new URL(page.url()).pathname)

  const cookies = (await page.context().cookies()).map((c) => c.name)
  for (const required of ['LH_access', 'LH_refresh', 'LH_session']) {
    if (!cookies.includes(required)) throw new Error('missing session cookie ' + required)
  }
  const access = (await page.context().cookies()).find((c) => c.name === 'LH_access')
  if (!access.httpOnly) throw new Error('LH_access must be httpOnly')

  const me = await page.evaluate(async () => {
    const t = (await (await fetch('/api/auth/refresh', { credentials: 'include' })).json()).access_token
    const r = await fetch('/api/v1/users/session', { headers: { Authorization: 'Bearer ' + t } })
    return { status: r.status, body: await r.json() }
  })
  const roles = (me.body.roles || []).map((r) => r.role && r.role.role_uuid)
  log('session', me.status, me.body.user && me.body.user.email, roles.join(','))
  if (me.status !== 200 || me.body.user?.email !== 'alice@example.com') throw new Error('not signed in as alice')
  if (!roles.includes('role_global_admin')) throw new Error('group -> role mapping not applied')

  const slo = await page.evaluate(async () => (await fetch('/api/auth/authentik/logout-url')).json())
  if (!slo.url || !slo.url.includes('/end-session/') || !slo.url.includes('client_id=')) throw new Error('bad logout url')
  log('single-logout url ok')

  // A second, unbound callback replay must be refused (single-use state).
  const replay = await page.evaluate(async () => (await fetch('/api/auth/authentik/callback', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ code: 'x', state: 'A'.repeat(43), binding: 'y' }),
  })).status)
  if (replay !== 400) throw new Error('forged callback was not refused: ' + replay)
  // Hand the signed-in session to the live-class test.
  if (statePath) await page.context().storageState({ path: statePath })
  log('E2E OK')
} catch (e) {
  await page.screenshot({ path: `${outDir}/authentik-e2e-failure.png` }).catch(() => {})
  console.error('E2E FAILED:', e.message)
  process.exitCode = 1
} finally {
  await browser.close()
}
}

main().catch((e) => {
  console.error('E2E FAILED:', e.message)
  process.exitCode = 1
})
