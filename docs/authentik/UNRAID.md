# Running on Unraid (amd64)

The image is `ghcr.io/aviator2276/learnhouse:latest`: the whole LearnHouse app
(web, API, collaboration server, internal nginx) in one container. It needs a
PostgreSQL **with pgvector** and a Redis next to it, and a reverse proxy with
TLS in front (Authentik requires https redirect URIs; LearnHouse marks session
cookies `Secure`).

```
browser ──https──► reverse proxy (SWAG / NPM / Traefik) ──http──► learnhouse :8080 → :80
                                                                      │
                                                     learnhouse-db (pgvector)   learnhouse-redis
```

## Option A: Docker Compose Manager (recommended)

1. Install **Docker Compose Manager** from Community Applications.
2. *Docker → Compose → Add New Stack* → name `learnhouse`.
3. Paste `deploy/docker-compose.yml` as the compose file and your filled-in
   copy of `deploy/.env.example` as the `.env`.
   Generate every secret with `openssl rand -base64 32`.
4. *Compose Up*. First start takes about a minute (database setup); the
   container turns *healthy* when ready.
5. Point your reverse proxy at `http://<unraid-ip>:8080` for
   `learn.example.com` (WebSocket support on, for `/collab`).
6. Configure Authentik ([README.md](README.md)), then sign in at
   `https://learn.example.com/login` with the initial admin and allow
   Authentik in *Dashboard → Users → Sign-in methods* if you want to make it
   the only method.

## Option B: Unraid templates (no compose)

1. Create a custom network once (Unraid terminal):

   ```bash
   docker network create learnhouse
   ```

2. Add the dependencies from Community Applications or by hand, both on the
   `learnhouse` network, no published ports:
   - `pgvector/pgvector:pg16` named `learnhouse-db`, env `POSTGRES_USER=learnhouse`,
     `POSTGRES_PASSWORD=<secret>`, `POSTGRES_DB=learnhouse`, path
     `/var/lib/postgresql/data` → `/mnt/user/appdata/learnhouse/postgres`.
   - `redis:7.4-alpine` named `learnhouse-redis`, path `/data` →
     `/mnt/user/appdata/learnhouse/redis`, post arguments
     `redis-server --appendonly yes`.
3. Copy `deploy/unraid/learnhouse-authentik.xml` to
   `/boot/config/plugins/dockerMan/templates-user/` on the flash drive.
4. *Docker → Add Container → Template: learnhouse-authentik*, fill in the
   fields (secrets are masked), *Apply*.

## Live classes (plugNmeet)

If you run plugNmeet (for example on the same Unraid box), fill in the
*plugNmeet* fields of the template, or the `LEARNHOUSE_PLUGNMEET_*` lines of
`.env`. Use the LAN address for the server URL (`http://<unraid-ip>:8080`)
and the public https address for the client URL. See
[../plugnmeet/README.md](../plugnmeet/README.md).

## Updates

New images are published automatically (every LearnHouse release, plus a
weekly rebuild for OS security patches). To pick them up:

- **Unraid UI**: *Docker → Check for Updates → apply update*, or
- **CA Auto Update Applications** plugin: enable it for `learnhouse` (daily), or
- **Watchtower** (compose only): `docker compose --profile autoupdate up -d`
  updates just the LearnHouse container at 04:30.

Database migrations run automatically when the new container starts. For a
cautious setup, pin a version instead of `latest` (e.g. `:1.4.4`) and bump it
yourself; every published version stays available for rollback.

## Backups

Back up `/mnt/user/appdata/learnhouse/` (content, postgres, redis), ideally
with the stack stopped or with `pg_dump`:

```bash
docker exec learnhouse-db pg_dump -U learnhouse learnhouse | gzip > /mnt/user/backups/learnhouse-$(date +%F).sql.gz
```

## Troubleshooting: "We couldn't find that" on every page

`NEXT_PUBLIC_LEARNHOUSE_DEFAULT_ORG` names an organization that does not
exist. The organization is created once, on the first start, from
`LEARNHOUSE_INITIAL_ORG_SLUG` (default `default`); changing either variable
later does not rename it. Check the real slug:

```bash
docker exec learnhouse-db psql -U learnhouse -d learnhouse -c "select id, slug, name from organization;"
```

Then either set `NEXT_PUBLIC_LEARNHOUSE_DEFAULT_ORG` to that slug (or remove
it: LearnHouse then finds the org itself) and restart, or rename the org:

```bash
docker exec learnhouse-db psql -U learnhouse -d learnhouse -c "UPDATE organization SET slug='cc' WHERE slug='default';"
docker exec learnhouse-redis redis-cli FLUSHALL   # clears cached lookups; signs everyone out
docker restart learnhouse
```

## Troubleshooting: "Organization not found" although the org exists

If `select slug from organization` shows the slug you configured and
`curl http://<unraid-ip>:<port>/api/v1/instance/info` reports it as
`default_org_slug`, the web server cannot reach the API through your public
domain. By default it renders pages by calling `NEXT_PUBLIC_LEARNHOUSE_API_URL`
(e.g. `https://learn.example.com/api/v1/`), i.e. from inside the container
out to the internet and back. Check:

```bash
docker exec learnhouse curl -s -o /dev/null -w "%{http_code}\n" https://learn.example.com/api/v1/instance/info
```

Anything but `200` (DNS not pointing here yet, reverse proxy not set up,
Cloudflare in front, a router without hairpin NAT) breaks page rendering.

1. Set `LEARNHOUSE_INTERNAL_API_URL=http://127.0.0.1:9000` (already in the
   compose file and the Unraid template). Page rendering then stays inside the
   container.
2. Sign-in requests still go through the public URL. If the check above is
   not `200`, make the domain resolve to your reverse proxy *from inside the
   container*: Unraid **Extra Parameters** `--add-host learn.example.com:<reverse-proxy-LAN-IP>`
   (compose: `extra_hosts: ["learn.example.com:<reverse-proxy-LAN-IP>"]`).
3. Browse to the site through that https domain, not `http://<unraid-ip>:<port>`:
   session cookies are `Secure`, so logins do not stick over plain http.

## Hardening checklist

- Only the reverse proxy is exposed to the internet; database and Redis have
  no published ports.
- `LEARNHOUSE_DEVELOPMENT_MODE=False` (the default).
- Change the initial admin password after first login, or disable password
  sign-in for your organization once Authentik works.
- Keep the Authentik client secret only in the container env (masked in the
  Unraid template) or in a mounted file via `LEARNHOUSE_AUTHENTIK_CLIENT_SECRET_FILE`.
- Verify image signatures if you like:
  see [CI-CD.md](CI-CD.md#verifying-a-published-image).
