# Email notifications for communities and assignments

Members get an email when something happens that concerns them: a new post
from an instructor, a mention, a reply, a weekly digest of the most active
discussions, a new assignment, a due date that was set or moved, and a
reminder before an assignment is due.

This is a fork-only feature. It does not use or unlock LearnHouse's
Enterprise features, and it is independent of Authentik and plugNmeet.

## What members choose

Profile picture → **Email notifications**. On a phone it is in the **More**
sheet under *You*. Every email also links to it. Changes save right away.

### Communities

| Level | What is emailed |
| --- | --- |
| Highlights (default) | Posts from instructors, mentions, and comments in discussions the member started or joined. |
| Everything | Every new post and comment. |
| Mentions and replies | Mentions, and comments in discussions the member started or joined. |
| Mentions only | Posts and comments that contain `@` and the member's username. |
| Off | Nothing, weekly digest included. |

- **Weekly digest**: on or off, separate from the level (off when the level
  is Off).
- **Communities to follow**: turn one off to stop every email about it,
  mentions included.
- **Post types** (General, Q&A, Ideas, Announcements, Show and Tell): turning
  one off hides new posts of that type and leaves them out of the digest.
  Mentions and replies still arrive.

An instructor is an organization admin or maintainer, anyone whose role may
create courses (the built-in Instructor role), and, in a course's community,
the authors of that course. A learner labelling a post "Announcement" does
not make it one.

A mention is plain text: `@username`. It works in a post's title, its body
and in comments.

### Assignments

- **New assignments and due dates**: when an assignment is published, or its
  due date is set or moved.
- **Due date reminders**: before the due date, only if the member has not
  submitted. The member picks 1, 2 or 3 days or 1 week before.

Only learners enrolled in the course get these.

## What admins set

Profile picture → **Email notifications** → **Organization settings**, or
`/dash/email-notifications`. Organization admins and maintainers only.

- Turn all notification emails of the organization on or off.
- The level members start with.
- Weekly digest: on or off, the day, the hour and the time zone.
- Assignment emails: on or off, and the default reminder time.
- **Send a test email**: New post, Weekly digest or Assignment reminder. It
  goes to your own address only, at most 5 times in 10 minutes. It uses this
  week's discussions when there are any you can read, otherwise a sample.

## Setup

1. Outgoing email must work. LearnHouse sends through SMTP or Resend:

   ```
   LEARNHOUSE_EMAIL_PROVIDER=smtp
   LEARNHOUSE_SYSTEM_EMAIL_ADDRESS=learnhouse@example.com
   LEARNHOUSE_SMTP_HOST=smtp.example.com
   LEARNHOUSE_SMTP_PORT=587
   LEARNHOUSE_SMTP_USERNAME=learnhouse@example.com
   LEARNHOUSE_SMTP_PASSWORD=CHANGE_ME
   LEARNHOUSE_SMTP_USE_TLS=true
   ```

2. Tell LearnHouse its public address, so links in emails are right:

   ```
   LEARNHOUSE_PLATFORM_URL=https://learn.example.com
   ```

   Without it and without `LEARNHOUSE_DOMAIN`, no notification email is sent
   (the log says so) and the test button answers with an error.

3. Update the container. The database tables are added on start. Open
   `/dash/email-notifications` and press a test button.

Optional:

| Variable | Default | |
| --- | --- | --- |
| `LEARNHOUSE_NOTIFICATION_EMAILS_DISABLED` | unset | `true` stops every notification email of the server. |
| `LEARNHOUSE_COMMUNITY_EMAILS_DELAY_MINUTES` | `10` | How long a new post waits before it is emailed, so the author can fix or delete it. 0 to 1440. |

## How it behaves

- **After an update, nobody is emailed about what is already there.** The
  first run only records the newest post and the current state of every
  assignment.
- New posts are checked every five minutes. A member gets one email per
  run, listing every discussion with news for them.
- Posts older than two days when the server gets to them are skipped (for
  example after the server was down).
- The digest goes out on the chosen day once the chosen hour has passed. A
  member with nothing new to read that week gets none.
- A due date without a time counts for the whole day. "1 day before" a
  Friday deadline is Thursday.
- No one is emailed about their own post.

## Security

- A member is only emailed about a community LearnHouse lets them read:
  members of the organization, and for a community tied to user groups only
  those groups and community managers. Tests check this against LearnHouse's
  own access rule. Assignment emails ask that rule for every learner.
- People outside the organization are never emailed, public community or
  not.
- Emails go only to verified addresses, and never to an address that
  unsubscribed from LearnHouse email or bounced.
- Post text is escaped and shortened to a preview. Titles and names cannot
  inject links or email headers.
- The unsubscribe link carries a signature made with the server's secret,
  bound to one member and one organization. It can only turn emails off.
  Opening the link changes nothing (mail scanners open links); the button on
  the page does. Mail apps' own "Unsubscribe" button works too.
- Settings endpoints need a signed-in member; organization settings and test
  emails need an admin or maintainer. API tokens are refused.
- Each job records what it is about to send before sending, so a restart,
  a mail outage or a second replica cannot send an email twice.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Test button says the email could not be sent | SMTP settings. The API log has the provider's answer. |
| Test button mentions a public address | Set `LEARNHOUSE_PLATFORM_URL`. |
| Test arrives, real emails do not | The member's level and muted communities; whether their address is verified; the 10 minute delay; `Notification emails:` lines in the API log. |
| No digest | It needs at least one post or comment in the last 7 days, and the chosen day and hour in the chosen time zone. |
| No assignment emails | The learner must be enrolled, and both the assignment and its activity published. |
