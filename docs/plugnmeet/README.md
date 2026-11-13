# Live classes with plugNmeet

Instructors schedule live classes. Members see them on the **Live classes**
page, the **calendar**, the **course page** and a **live-now banner**, and
join with one click. The video room is your own self-hosted
[plugNmeet](https://www.plugnmeet.org) server. LearnHouse decides who may
enter and with which role; plugNmeet never sees a LearnHouse password.

This is a fork-only feature. It does not use or unlock LearnHouse's
Enterprise "Live sessions", and it is independent of Authentik sign-in.

## What you get

| | |
| --- | --- |
| Schedule a class | Title, description, start, duration (15 min – 8 h), in your own time zone. Shown to everyone in theirs. |
| Recurring classes | Every day, week or month, 2–52 classes. A weekly class stays at the same local time across daylight-saving changes. Edit, cancel or delete one class or *this and following*. |
| Course classes | Scheduled from the course page; only people who can open the course see them. |
| Organization classes | Scheduled from **Live classes**; any member sees them. |
| Meeting room | One org-wide room that admins and maintainers open on demand, for quick meetings. |
| Where members find it | **Live classes** in the top menu (happening now, upcoming, past), the **calendar** (`/calendar`), a box on the **course page**, and a banner on every page while a class is live or starting within 10 minutes. |
| Leave returns to LearnHouse | plugNmeet's *Leave* button sends people back to the class page. |
| Attendance | With the webhook on: who joined, when, and for how long, on the class page (hosts only). |

The room itself uses these plugNmeet features: webcams, screen sharing, chat
with file upload, whiteboard, shared notes, polls, six breakout rooms, cloud
and local recording. Everyone starts muted, and there is no waiting room.

## Who can do what

Only **signed-in members of the organization** can see or join anything.
Guests, anonymous visitors, API tokens and members of other organizations get
nothing; the API answers them as if the class did not exist.

| | Organization class | Course class |
| --- | --- | --- |
| See it and join once it is live | every member | members who can open the course (LearnHouse's own course access rules) |
| Schedule, edit, cancel, delete, start, end, see attendance | admins and maintainers | whoever can edit the course (its authors and contributors, admins, maintainers) |
| Enters plugNmeet as | participant | participant |
| Hosts enter as | moderator (can mute, remove, record, run breakout rooms) | moderator |

Rooms only open when a host clicks **Start class**, so learners cannot sit in
an empty room unsupervised. A host can open the room up to 60 minutes before
the start and until 60 minutes after the end
(`LEARNHOUSE_PLUGNMEET_START_EARLY_MINUTES` / `_END_GRACE_MINUTES`).

## Setup

### 1. plugNmeet

You need a working plugNmeet server. Check that `bash pnm-test.sh` on the
plugNmeet host prints join URLs that work in two browsers. From
plugNmeet's `.env` take `PLUG_N_MEET_API_KEY` (default `plugnmeet`) and
`PLUG_N_MEET_SECRET`.

### 2. LearnHouse

Add these to the LearnHouse container (Unraid template fields, or
`deploy/.env`), then restart it:

```env
LEARNHOUSE_PLUGNMEET_ENABLED=true
# How the LearnHouse server reaches plugNmeet. On the same Unraid box or LAN,
# use the LAN address: it skips the trip through Cloudflare.
LEARNHOUSE_PLUGNMEET_SERVER_URL=http://192.168.1.20:8080
# What browsers open: always the public https address.
LEARNHOUSE_PLUGNMEET_CLIENT_URL=https://meet.example.com
LEARNHOUSE_PLUGNMEET_API_KEY=plugnmeet
LEARNHOUSE_PLUGNMEET_API_SECRET=<PLUG_N_MEET_SECRET>
```

**Live classes** then appears in the top menu for signed-in members.

### 3. Attendance (optional)

Turn the webhook on in LearnHouse:

```env
LEARNHOUSE_PLUGNMEET_WEBHOOK_ENABLED=true
```

Then point plugNmeet at it in its `config.yaml` and restart plugNmeet
(`docker compose restart plugnmeet`):

```yaml
client:
  webhook_conf:
    enable: true
    url: "https://learn.example.com/api/v1/plugnmeet/webhook"
    enable_for_per_meeting: false
```

On the same LAN you can use LearnHouse's LAN address instead, for example
`http://192.168.1.20:7000/api/v1/plugnmeet/webhook`.

### All settings

| Variable | Default | |
| --- | --- | --- |
| `LEARNHOUSE_PLUGNMEET_ENABLED` | `false` | Master switch. Off: no menu, no pages, every endpoint returns 404. |
| `LEARNHOUSE_PLUGNMEET_SERVER_URL` | (required) | Server-to-server address. Plain `http` is allowed only for LAN hosts: private or loopback IPs, single-label container names, `host.docker.internal`, and `.local`, `.lan`, `.internal` or `.home.arpa` names. Anything else must be `https`. |
| `LEARNHOUSE_PLUGNMEET_CLIENT_URL` | (required) | Browser-facing plugNmeet URL, `https` only. |
| `LEARNHOUSE_PLUGNMEET_API_KEY` | (required) | `PLUG_N_MEET_API_KEY`. |
| `LEARNHOUSE_PLUGNMEET_API_SECRET` / `_FILE` | (required) | `PLUG_N_MEET_SECRET`, or a file holding it. At least 16 characters. |
| `LEARNHOUSE_PLUGNMEET_PUBLIC_URL` | `https://$LEARNHOUSE_DOMAIN` | LearnHouse's public origin, for *Leave* and avatar URLs. |
| `LEARNHOUSE_PLUGNMEET_WEBHOOK_ENABLED` | `false` | Accept signed attendance webhooks. |
| `LEARNHOUSE_PLUGNMEET_QUICK_ROOM` | `true` | The org-wide meeting room. |
| `LEARNHOUSE_PLUGNMEET_EMPTY_TIMEOUT` | `1800` | Seconds plugNmeet keeps an empty room. |
| `LEARNHOUSE_PLUGNMEET_START_EARLY_MINUTES` | `60` | How early hosts may open a room. |
| `LEARNHOUSE_PLUGNMEET_END_GRACE_MINUTES` | `60` | How long after the end hosts may still open it. |
| `LEARNHOUSE_PLUGNMEET_CA_BUNDLE` | | PEM file for a privately-signed plugNmeet certificate. |
| `LEARNHOUSE_PLUGNMEET_HTTP_TIMEOUT` | `15` | Seconds, 1–60. |
| `LEARNHOUSE_PLUGNMEET_ALLOW_INSECURE_HTTP` | `false` | Allow `http` everywhere. Local testing only. |

A broken configuration (missing secret, `http` client URL...) turns the
feature off and logs one error line. It never takes LearnHouse down.

## Security model

- **The secret never leaves the API.** Every call to plugNmeet is
  HMAC-SHA256 signed over the exact bytes sent. The secret is not stored in
  the database, returned by an endpoint or logged.
- **Roles are decided by LearnHouse.** The API mints a fresh, single-use join
  token per click, with the role baked in. The browser never builds or sees
  anything it could change to become a moderator. The token goes straight to
  `window.location` and the frontend only follows `https` links.
- **Members only.** Every endpoint checks the signed-in session (no API
  tokens), membership of the class's organization, and for course classes
  LearnHouse's own course access check. Non-members get 404, not 403, so
  class IDs reveal nothing.
- **Stable identities.** The plugNmeet user ID is the LearnHouse user UUID.
  A reconnect replaces the old session instead of duplicating it, and
  attendance maps straight back. Emails are never sent to plugNmeet.
- **Webhooks are verified before they are read.** The body is limited to
  64 KB. The signature is an HS256 JWT keyed with the API secret
  (`iss` = API key, expiry required) whose `sha256` claim must match the body.
  Each token is accepted once, through a Redis replay cache; without Redis,
  webhooks are refused. Only LearnHouse members of the class's organization
  are recorded, at the server's own receive time.
- **Outages are not hidden.** If plugNmeet is unreachable, *Join* says so,
  instead of pretending the class has not started.
- Joining is rate limited to 20 attempts per user per minute.

## How it keeps up with LearnHouse updates

Almost everything is fork-owned and new, so upstream changes rarely touch it:

- API: `apps/api/src/services/plugnmeet/`, `src/routers/plugnmeet.py`,
  `src/db/live_classes.py`, one migration.
- Web: `apps/web/components/LiveClasses/`, `services/plugnmeet/`, the
  `/live-classes` pages.
- The calendar entries use the calendar's own plug-in registry. No calendar
  code is changed.

The only upstream files touched are one line each, marked `Fork:`:
`src/router.py` (mount), `OrgMenuLinks.tsx` (menu entry), the
`(withmenu)/layout.tsx` (banner) and `course/[courseuuid]/course.tsx` (course
panel). If upstream rewrites one of them, the automatic sync opens an issue,
and the fix is to put that line back.

Database migrations: the fork's migrations sit in one line after upstream's.
When an upstream release brings migrations of its own, the sync job adds an
empty Alembic merge revision automatically (`.fork/merge_migration_heads.py`),
so the graph keeps the single head that upstream's tests require.

CI covers it end to end. There are unit tests at 100% coverage against a
plugNmeet fake that checks signatures, a migration test on Postgres, and
container boot checks (no secret leak; anonymous and unsigned requests
refused). A browser e2e on the built image schedules a weekly series in the
UI, finds it on the list, calendar and banner, starts it, opens the meeting
room and sends signed and forged attendance webhooks. It runs against a
stand-in that verifies requests like plugNmeet does, because a full plugNmeet
stack does not fit a free Actions runner.

## Not included (yet)

- Recordings are made and kept by plugNmeet (`recording_files` on the
  plugNmeet host); there is no LearnHouse UI to list them.
- Other providers (Zoom, Google Meet) and user-group-restricted classes.
- Interface text is English (no translations yet).
- *Leave* returns to `PUBLIC_URL/live-classes/...`, which suits
  single-organization installs (the default). Multi-organization installs
  with per-org subdomains land on the main domain.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| No **Live classes** in the menu | `LEARNHOUSE_PLUGNMEET_ENABLED=true`, then check `docker logs learnhouse \| grep plugNmeet` for the config error. `curl https://learn.example.com/api/v1/plugnmeet/config` must say `"enabled":true`. |
| "The meeting server can't be reached" | LearnHouse cannot reach `SERVER_URL`. Check with `docker exec learnhouse curl -s -o /dev/null -w '%{http_code}\n' http://192.168.1.20:8080/healthCheck` (expect 200). A Docker network may need `--add-host` or the LAN IP rather than `localhost`. |
| Join works but the room never loads | plugNmeet side: `livekit_info.host` must be the public URL; `/ws` and `/livekit` must route (see the plugNmeet stack docs). |
| "invalid signature" in plugNmeet logs, joins fail | `API_KEY`/`API_SECRET` do not match plugNmeet's `.env`. |
| No attendance | `WEBHOOK_ENABLED=true` in LearnHouse **and** `webhook_conf.enable: true` with the URL in plugNmeet's `config.yaml`. `docker logs learnhouse \| grep "plugNmeet webhook"` shows rejected attempts. |
