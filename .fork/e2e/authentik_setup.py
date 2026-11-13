"""Configure a fresh Authentik exactly as docs/authentik/README.md describes.

Prints {"client_id", "client_secret"} as JSON. Standard library only.
Usage: python3 authentik_setup.py AK_INTERNAL_URL AK_PUBLIC_HOST LH_PUBLIC_URL LH_BACKCHANNEL_URL TOKEN
"""

import json
import sys
import time
import urllib.error
import urllib.request

AK, AK_HOST, LH, BACKCHANNEL, TOKEN = sys.argv[1:6]
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def api(method, path, body=None):
    req = urllib.request.Request(
        AK + "/api/v3" + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json", "Host": AK_HOST},
    )
    try:
        with opener.open(req, timeout=30) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        if e.code not in (401, 403):
            sys.stderr.write(f"{method} {path} -> {e.code}: {e.read()[:500]!r}\n")
        raise


def first(path, tries=60):
    # Default flows and keys are created by the worker's blueprints a little
    # after the server reports ready.
    for _ in range(tries):
        try:
            results = api("GET", path)["results"]
        except urllib.error.HTTPError as e:
            if e.code not in (401, 403):  # bootstrap token not created yet
                raise
            results = []
        if results:
            return results[0]
        time.sleep(5)
    raise SystemExit(f"Authentik never provided {path}")


authz = first("/flows/instances/?slug=default-provider-authorization-implicit-consent")["pk"]
inval = first("/flows/instances/?slug=default-provider-invalidation-flow")["pk"]
key = first("/crypto/certificatekeypairs/?name=authentik%20Self-signed%20Certificate")["pk"]
NEEDED_SCOPES = [f"goauthentik.io/providers/oauth2/scope-{s}" for s in ("openid", "profile")]
for _ in range(60):  # also created by blueprints, possibly after the flows
    scopes = {m.get("managed"): m["pk"] for m in api("GET", "/propertymappings/provider/scope/?page_size=200")["results"]}
    if all(k in scopes for k in NEEDED_SCOPES):
        break
    time.sleep(5)
else:
    raise SystemExit("Authentik never created its default scope mappings")

# The documented "verified email" mapping (Authentik's default sends false).
email_mapping = api(
    "POST",
    "/propertymappings/provider/scope/",
    {
        "name": "LearnHouse: email (verified)",
        "scope_name": "email",
        "expression": 'return {\n    "email": request.user.email,\n    "email_verified": True,\n}',
    },
)["pk"]

provider = api(
    "POST",
    "/providers/oauth2/",
    {
        "name": "LearnHouse",
        "authorization_flow": authz,
        "invalidation_flow": inval,
        "client_type": "confidential",
        "client_id": "learnhouse-e2e",
        "grant_types": ["authorization_code"],
        "redirect_uris": [{"matching_mode": "strict", "url": LH + "/auth/callback/authentik"}],
        "signing_key": key,
        "property_mappings": [
            scopes["goauthentik.io/providers/oauth2/scope-openid"],
            scopes["goauthentik.io/providers/oauth2/scope-profile"],
            email_mapping,
        ],
        "sub_mode": "hashed_user_id",
        "include_claims_in_id_token": True,
        "logout_uri": BACKCHANNEL,
        "logout_method": "backchannel",
    },
)
api("POST", "/core/applications/", {"name": "LearnHouse", "slug": "learnhouse", "provider": provider["pk"]})
group = api("POST", "/core/groups/", {"name": "lh-admins"})
user = api(
    "POST",
    "/core/users/",
    {"username": "alice", "name": "Alice Liddell", "email": "alice@example.com", "is_active": True, "groups": [group["pk"]]},
)
api("POST", f"/core/users/{user['pk']}/set_password/", {"password": "Alice-Passw0rd!"})
print(json.dumps({"client_id": provider["client_id"], "client_secret": provider["client_secret"]}))
