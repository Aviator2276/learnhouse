#!/usr/bin/env bash
# Real end-to-end test: the LearnHouse image against a real Authentik, and
# its plugNmeet live classes against a signature-checking plugNmeet stand-in.
#
#   .fork/e2e/run.sh <learnhouse-image> [authentik-version]
#
# Starts Postgres, Redis, Authentik (server + worker), the plugNmeet stand-in
# and LearnHouse on a private Docker network, configures Authentik as
# docs/authentik/README.md says, signs in through a real browser, schedules
# and starts a live class in the UI, sends signed attendance webhooks, then
# ends the session in Authentik and checks the back-channel logout revoked
# it in LearnHouse.
# Needs: docker, python3, node + playwright (chromium).
set -euo pipefail

IMAGE="${1:?usage: run.sh <learnhouse-image> [authentik-version]}"
AK_VERSION="${2:-2026.8.5}"
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="${E2E_OUT:-/tmp/authentik-e2e}"
NET=lh-e2e
SUBNET=172.30.0.0/24
LH_IP=172.30.0.10
AK_IP=172.30.0.11
PNM_IP=172.30.0.12
# Any image with python3 runs the stand-in; default is the base image the build already uses.
PNM_IMAGE="${PNM_IMAGE:-python:3.14.10-alpine3.24}"
PNM_SECRET="e2e-pnm-secret-$(openssl rand -hex 16)"
STATE_DIR=$(mktemp -d)
TOKEN="e2e-bootstrap-token-$(openssl rand -hex 12)"
mkdir -p "$OUT"

cleanup() {
  if [ "${KEEP:-0}" != "1" ]; then
    docker rm -f e2e-lh e2e-db e2e-redis e2e-ak-db e2e-ak-server e2e-ak-worker e2e-pnm >/dev/null 2>&1 || true
    docker network rm "$NET" >/dev/null 2>&1 || true
  fi
  rm -rf "$STATE_DIR"
}
trap cleanup EXIT
dump_logs() {
  echo "----- learnhouse -----"; docker logs e2e-lh 2>&1 | grep -v "alembic.ddl" | tail -80 || true
  echo "----- plugnmeet stand-in -----"; docker logs e2e-pnm 2>&1 | tail -20 || true
  echo "----- authentik -----"; docker logs e2e-ak-server 2>&1 | grep -E '"level": "(warning|error)"' | tail -20 | cut -c1-400 || true
}

wait_http() { # url, expected code, tries
  for _ in $(seq 1 "${3:-60}"); do
    [ "$(curl --noproxy '*' -s -o /dev/null -w '%{http_code}' --max-time 3 "$1" || true)" = "$2" ] && return 0
    sleep 5
  done
  return 1
}

cleanup
docker network create --subnet "$SUBNET" "$NET" >/dev/null

# --- Authentik ------------------------------------------------------------------
docker run -d --name e2e-ak-db --network "$NET" -e POSTGRES_USER=ak -e POSTGRES_PASSWORD=ak -e POSTGRES_DB=authentik postgres:16-alpine >/dev/null
AK_ENV=(
  -e AUTHENTIK_SECRET_KEY="$(openssl rand -hex 32)"
  -e AUTHENTIK_POSTGRESQL__HOST=e2e-ak-db -e AUTHENTIK_POSTGRESQL__USER=ak
  -e AUTHENTIK_POSTGRESQL__PASSWORD=ak -e AUTHENTIK_POSTGRESQL__NAME=authentik
  -e AUTHENTIK_BOOTSTRAP_PASSWORD="$(openssl rand -hex 16)" -e AUTHENTIK_BOOTSTRAP_TOKEN="$TOKEN"
  -e AUTHENTIK_BOOTSTRAP_EMAIL=akadmin@example.com
  -e AUTHENTIK_DISABLE_UPDATE_CHECK=true -e AUTHENTIK_ERROR_REPORTING__ENABLED=false
  -e AUTHENTIK_LISTEN__HTTP=0.0.0.0:9000 -e AUTHENTIK_LISTEN__HTTPS=0.0.0.0:9443
  -e AUTHENTIK_LISTEN__METRICS=0.0.0.0:9300
)
docker run -d --name e2e-ak-server --network "$NET" --ip "$AK_IP" "${AK_ENV[@]}" "ghcr.io/goauthentik/server:${AK_VERSION}" server >/dev/null
docker run -d --name e2e-ak-worker --network "$NET" "${AK_ENV[@]}" "ghcr.io/goauthentik/server:${AK_VERSION}" worker >/dev/null

# --- LearnHouse dependencies --------------------------------------------------------
docker run -d --name e2e-db --network "$NET" -e POSTGRES_USER=lh -e POSTGRES_PASSWORD=lh -e POSTGRES_DB=lh pgvector/pgvector:pg16 >/dev/null
docker run -d --name e2e-redis --network "$NET" redis:7.4-alpine >/dev/null
docker run -d --name e2e-pnm --network "$NET" --ip "$PNM_IP" -v "$HERE/fake_plugnmeet.py:/fake.py:ro" \
  --entrypoint python3 "$PNM_IMAGE" -I /fake.py 8080 plugnmeet "$PNM_SECRET" "http://${LH_IP}/api/v1/plugnmeet/webhook" >/dev/null

echo "Waiting for Authentik ${AK_VERSION}..."
if ! wait_http "http://${AK_IP}:9000/-/health/ready/" 200 90; then dump_logs; exit 1; fi
CREDS=$(python3 -I "$HERE/authentik_setup.py" "http://${AK_IP}:9000" "auth.localhost:9000" \
  "http://learn.localhost" "http://${LH_IP}/api/v1/auth/authentik/backchannel-logout" "$TOKEN")
CLIENT_ID=$(echo "$CREDS" | python3 -c 'import json,sys;print(json.load(sys.stdin)["client_id"])')
CLIENT_SECRET=$(echo "$CREDS" | python3 -c 'import json,sys;print(json.load(sys.stdin)["client_secret"])')
echo "Authentik configured (client ${CLIENT_ID})"

# --- LearnHouse -------------------------------------------------------------------
docker run -d --name e2e-lh --network "$NET" --ip "$LH_IP" \
  --add-host auth.localhost:"$AK_IP" --add-host learn.localhost:127.0.0.1 \
  -e LEARNHOUSE_SQL_CONNECTION_STRING=postgresql://lh:lh@e2e-db:5432/lh \
  -e LEARNHOUSE_REDIS_CONNECTION_STRING=redis://e2e-redis:6379/0 -e LEARNHOUSE_REDIS_URL=redis://e2e-redis:6379/0 \
  -e LEARNHOUSE_AUTH_JWT_SECRET_KEY="$(openssl rand -hex 32)" -e NEXTAUTH_SECRET="$(openssl rand -hex 32)" \
  -e COLLAB_INTERNAL_KEY="$(openssl rand -hex 16)" \
  -e LEARNHOUSE_DOMAIN=learn.localhost -e LEARNHOUSE_INITIAL_ADMIN_EMAIL=admin@example.com \
  -e LEARNHOUSE_INITIAL_ADMIN_PASSWORD="E2e-$(openssl rand -hex 8)-Pw!" \
  -e LEARNHOUSE_INITIAL_ORG_NAME=E2E -e LEARNHOUSE_INITIAL_ORG_SLUG=default \
  -e NEXT_PUBLIC_LEARNHOUSE_API_URL=http://learn.localhost/api/v1/ -e NEXT_PUBLIC_LEARNHOUSE_BACKEND_URL=http://learn.localhost/ \
  -e NEXT_PUBLIC_LEARNHOUSE_DOMAIN=learn.localhost -e NEXT_PUBLIC_LEARNHOUSE_TOP_DOMAIN=localhost \
  -e NEXT_PUBLIC_LEARNHOUSE_MULTI_ORG=False -e NEXT_PUBLIC_LEARNHOUSE_DEFAULT_ORG=default \
  -e NEXT_PUBLIC_LEARNHOUSE_HTTPS=False -e NEXTAUTH_URL=http://learn.localhost \
  -e HOSTNAME=0.0.0.0 -e LEARNHOUSE_API_URL=http://localhost:9000 \
  -e LEARNHOUSE_AUTHENTIK_ENABLED=true -e LEARNHOUSE_AUTHENTIK_ALLOW_INSECURE_HTTP=true \
  -e LEARNHOUSE_AUTHENTIK_ISSUER=http://auth.localhost:9000/application/o/learnhouse/ \
  -e LEARNHOUSE_AUTHENTIK_CLIENT_ID="$CLIENT_ID" -e LEARNHOUSE_AUTHENTIK_CLIENT_SECRET="$CLIENT_SECRET" \
  -e LEARNHOUSE_AUTHENTIK_REDIRECT_URI=http://learn.localhost/auth/callback/authentik \
  -e LEARNHOUSE_AUTHENTIK_ROLE_MAPPING=lh-admins=admin \
  -e LEARNHOUSE_PLUGNMEET_ENABLED=true -e LEARNHOUSE_PLUGNMEET_SERVER_URL="http://${PNM_IP}:8080" \
  -e LEARNHOUSE_PLUGNMEET_CLIENT_URL=https://meet.localhost -e LEARNHOUSE_PLUGNMEET_API_KEY=plugnmeet \
  -e LEARNHOUSE_PLUGNMEET_API_SECRET="$PNM_SECRET" -e LEARNHOUSE_PLUGNMEET_PUBLIC_URL=http://learn.localhost \
  -e LEARNHOUSE_PLUGNMEET_ALLOW_INSECURE_HTTP=true -e LEARNHOUSE_PLUGNMEET_WEBHOOK_ENABLED=true \
  "$IMAGE" >/dev/null
echo "Waiting for LearnHouse..."
if ! wait_http "http://${LH_IP}/api/v1/health" 200 60; then dump_logs; exit 1; fi

# --- Browser sign-in ------------------------------------------------------------------
if ! node "$HERE/login.cjs" "$LH_IP" "$AK_IP" "$OUT" "$STATE_DIR/state.json"; then dump_logs; exit 1; fi

LINK=$(docker exec e2e-db psql -U lh -d lh -tAc "select user_id || '|' || issuer from userexternalidentity")
echo "identity link: $LINK"
USER_ID=${LINK%%|*}
[ -n "$USER_ID" ] || { echo "no identity link stored"; exit 1; }

# --- Live classes (plugNmeet) ---------------------------------------------------------
if ! LIVE_OUT=$(node "$HERE/live.cjs" "$LH_IP" "$STATE_DIR/state.json" "$OUT"); then echo "$LIVE_OUT"; dump_logs; exit 1; fi
echo "$LIVE_OUT"
CLASS_ID=$(sed -n 's/^LIVE_CLASS_ID=//p' <<<"$LIVE_OUT")
ALICE_UUID=$(docker exec e2e-db psql -U lh -d lh -tAc "select user_uuid from \"user\" where id = ${USER_ID}")
# plugNmeet reserves "user_"; LearnHouse sends lh_<uuid> as the identity.
ALICE_PNM_ID="lh_${ALICE_UUID#user_}"
pnm() { curl --noproxy '*' -sf -X POST "http://${PNM_IP}:8080/__webhook" -H 'Content-Type: application/json' -d "$1"; }
joined=$(pnm "{\"event\":\"participant_joined\",\"room_id\":\"lh-${CLASS_ID}\",\"identity\":\"${ALICE_PNM_ID}\"}")
grep -q '"result": "joined"' <<<"$joined" || { echo "signed webhook not accepted: $joined"; dump_logs; exit 1; }
forged=$(pnm "{\"event\":\"participant_joined\",\"room_id\":\"lh-${CLASS_ID}\",\"identity\":\"${ALICE_PNM_ID}\",\"token\":\"forged\"}")
grep -q '"status": 401' <<<"$forged" || { echo "forged webhook not refused: $forged"; dump_logs; exit 1; }
stays=$(docker exec e2e-db psql -U lh -d lh -tAc "select count(*) from liveclassattendance a join liveclass c on c.id = a.class_id where c.class_uuid = 'liveclass_${CLASS_ID}' and a.user_id = ${USER_ID}")
[ "$stays" = "1" ] || { echo "attendance not recorded (rows: $stays)"; dump_logs; exit 1; }
state=$(curl --noproxy '*' -sf "http://${PNM_IP}:8080/__state")
python3 -I -c '
import json, sys
s = json.loads(sys.argv[1]); cid = sys.argv[2]
assert not s["rejected"], "plugNmeet refused signatures: %s" % s["rejected"]
room = s["rooms"]["lh-" + cid]["metadata"]
assert room["logout_url"] == "http://learn.localhost/live-classes/" + cid, room["logout_url"]
assert room["webhook_url"] == "http://learn.localhost/api/v1/plugnmeet/webhook", room
host = [t for t in s["tokens"] if t["room_id"] == "lh-" + cid][0]["user_info"]
assert host["is_admin"] is True and host["user_id"].startswith("lh_"), host
assert "lh-quick-1" in s["rooms"], "quick room was not created"
' "$state" "$CLASS_ID" || { echo "unexpected plugNmeet state: $state"; dump_logs; exit 1; }
echo "live classes: signed API calls, host token, attendance webhook and forged-webhook refusal OK"

# --- Back-channel logout: end the session in Authentik ------------------------------
for s in $(curl --noproxy '*' -sf -H "Authorization: Bearer $TOKEN" -H "Host: auth.localhost:9000" \
    "http://${AK_IP}:9000/api/v3/core/authenticated_sessions/?user__username=alice" \
    | python3 -c "import json,sys;print(' '.join(x['uuid'] for x in json.load(sys.stdin)['results']))"); do
  curl --noproxy '*' -sf -X DELETE -H "Authorization: Bearer $TOKEN" -H "Host: auth.localhost:9000" \
    "http://${AK_IP}:9000/api/v3/core/authenticated_sessions/$s/" >/dev/null
done
revoked=""
for _ in $(seq 1 30); do
  revoked=$(docker exec e2e-redis redis-cli GET "jwt_revoked_before:${USER_ID}")
  [ -n "$revoked" ] && break
  sleep 2
done
if [ -z "$revoked" ]; then echo "back-channel logout did not revoke the LearnHouse session"; dump_logs; exit 1; fi
echo "back-channel logout revoked sessions of user ${USER_ID}"
echo "E2E PASSED (Authentik ${AK_VERSION}, plugNmeet live classes)"
