"""Email notifications for communities and assignments. Fork-only.

Everything lives in this package, ``src/db/email_notifications.py`` and
``src/routers/email_notifications.py``. Upstream's community code is not
changed: new posts and comments are found by reading the tables after the
fact, so an upstream release cannot break the hook and a failed email can
never fail a post.

Three jobs run inside the API (no external cron):

* instant: every five minutes, email members about new posts and comments
  that match their level. One email per member and organization per run.
* digest: once a week per organization, the most active discussions.
* assignments: a new assignment, a due date that was set or moved, and a
  reminder before the due date, for learners enrolled in the course.

Who gets which community email::

    off         nothing, digest included
    mentions    posts and comments that @mention them
    replies     mentions, and comments in discussions they started or joined
    highlights  replies, and new posts from instructors (the default)
    all         every new post and comment

A member is only ever emailed about a community they can read, never about
their own post, and never at an unverified or suppressed address.
"""
