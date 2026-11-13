// Browser end-to-end for plugNmeet live classes, signed in as the Authentik
// admin from login.cjs: schedule a class in the UI, find it on the list,
// calendar and banner, start it (lands on the meeting client with a token),
// open the quick meeting room. Screenshots go to <out dir>.
// Usage: node live.cjs <LH_IP> <storage state file> <out dir>
const { chromium } = require('playwright')

const [lhIp, statePath, outDir = '.'] = process.argv.slice(2)
const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a)
const BASE = 'http://learn.localhost'

async function main() {
  const browser = await chromium.launch({
    args: ['--no-proxy-server', `--host-resolver-rules=MAP learn.localhost ${lhIp}`],
  })
  const context = await browser.newContext({ storageState: statePath, viewport: { width: 1280, height: 860 } })
  // The meeting client itself is not under test: answer for it.
  const joins = []
  await context.route('https://meet.localhost/**', (route) => {
    joins.push(route.request().url())
    return route.fulfill({ status: 200, contentType: 'text/html', body: '<h1>meeting client</h1>' })
  })
  const page = await context.newPage()
  const shot = (name) => page.screenshot({ path: `${outDir}/live-${name}.png`, fullPage: true }).catch(() => {})
  try {
    await page.goto(`${BASE}/live-classes`, { waitUntil: 'domcontentloaded' })
    await page.getByRole('heading', { name: 'Meeting room' }).waitFor({ timeout: 60000 })
    log('live classes page with meeting room')
    const menu = page.getByRole('link', { name: 'Live', exact: true }).first()
    if (!(await menu.count())) throw new Error('no Live classes menu entry')

    // Schedule a class through the dialog (default start: the next half hour).
    await page.getByRole('button', { name: 'Schedule class' }).click()
    await page.getByLabel('Title').fill('E2E live class')
    await page.getByLabel('Description').fill('Scheduled by the end-to-end test')
    await page.getByLabel('Repeat').selectOption('weekly')
    await page.getByLabel('Number of classes').fill('3')
    await shot('schedule-dialog')
    await page.getByRole('button', { name: 'Schedule', exact: true }).click()
    await page.getByText('3 classes scheduled').waitFor({ timeout: 20000 })
    const rows = page.getByRole('link', { name: /E2E live class/ })
    await rows.first().waitFor({ timeout: 20000 })
    if ((await rows.count()) !== 3) throw new Error(`expected 3 scheduled classes, saw ${await rows.count()}`)
    log('scheduled a weekly series of 3')
    await shot('list')

    // The class page; start the class.
    await rows.first().click()
    await page.getByRole('heading', { level: 1, name: 'E2E live class' }).waitFor({ timeout: 30000 })
    const classId = new URL(page.url()).pathname.split('/').pop()
    await shot('class-scheduled')
    await page.getByRole('button', { name: 'Start class' }).click()
    await page.waitForURL((u) => u.hostname === 'meet.localhost', { timeout: 30000 })
    const joinUrl = new URL(page.url())
    if (!joinUrl.searchParams.get('access_token')?.startsWith('e2e-token-')) throw new Error('no join token: ' + page.url())
    log('host landed on the meeting client with a token')

    // Back on LearnHouse it is live everywhere.
    await page.goto(`${BASE}/live-classes/${classId}`, { waitUntil: 'domcontentloaded' })
    await page.getByText('Live', { exact: true }).first().waitFor({ timeout: 30000 })
    await page.getByRole('button', { name: 'End for everyone' }).waitFor({ timeout: 30000 })
    await shot('class-live')
    log('class page shows Live and host controls')

    const token = await page.evaluate(async () => (await (await fetch('/api/auth/refresh', { credentials: 'include' })).json()).access_token)
    const events = await page.evaluate(async (t) => {
      const start = new Date(Date.now() - 86400000).toISOString()
      const end = new Date(Date.now() + 40 * 86400000).toISOString()
      const r = await fetch(`/api/v1/calendar/org/1/events?start=${start}&end=${end}`, { headers: { Authorization: 'Bearer ' + t } })
      return r.json()
    }, token)
    const onCalendar = events.filter((e) => e.type === 'live_session' && e.title === 'E2E live class')
    if (onCalendar.length !== 3) throw new Error('classes missing from the calendar: ' + JSON.stringify(events).slice(0, 300))
    log('all 3 classes are on the calendar')

    await page.goto(`${BASE}/calendar`, { waitUntil: 'domcontentloaded' })
    await page.getByText('E2E live class').first().waitFor({ timeout: 30000 })
    await shot('calendar')

    // The live-now banner on another page.
    await page.goto(`${BASE}/courses`, { waitUntil: 'domcontentloaded' })
    await page.getByText('Live now').waitFor({ timeout: 60000 })
    await shot('banner')
    log('live-now banner shown')

    // Quick meeting room.
    await page.goto(`${BASE}/live-classes`, { waitUntil: 'domcontentloaded' })
    await page.getByRole('button', { name: 'Open room' }).click()
    await page.waitForURL((u) => u.hostname === 'meet.localhost', { timeout: 30000 })
    log('quick meeting room opened')
    await page.goto(`${BASE}/live-classes`, { waitUntil: 'domcontentloaded' })
    await page.getByText('Open', { exact: true }).waitFor({ timeout: 30000 })
    await shot('quick-open')

    console.log(`LIVE_CLASS_ID=${classId}`)
    log('LIVE E2E OK')
  } catch (e) {
    await shot('failure')
    console.error('LIVE E2E FAILED:', e.message)
    process.exitCode = 1
  } finally {
    await browser.close()
  }
}

main().catch((e) => {
  console.error('LIVE E2E FAILED:', e.message)
  process.exitCode = 1
})
