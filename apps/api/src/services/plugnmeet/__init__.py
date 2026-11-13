"""Live classes on a self-hosted plugNmeet server.

Fork-local feature. Everything lives in this package, ``src/db/live_classes.py``
and ``src/routers/plugnmeet.py`` so upstream LearnHouse releases merge with as
few conflicts as possible. It does not use or unlock upstream's Enterprise
"live sessions" feature. See ``docs/plugnmeet/README.md`` for operator docs.

Auth model::

    browser ──POST /plugnmeet/classes/{uuid}/join (LearnHouse session)──▶ API
            API decides the role (moderator / participant) from LearnHouse
            RBAC, creates the room if a moderator is starting it, then asks
            plugNmeet for a single-use join token (HMAC-signed server call).
    browser ◀──────── {join_url} ── https://meet.example.com/?access_token=…

The API secret never leaves the API process, plugNmeet never sees a
LearnHouse credential, and a participant cannot raise their own role because
they never build the token. Webhooks back from plugNmeet (attendance) are
verified with the same secret (HS256 JWT carrying the body's SHA-256).
"""
