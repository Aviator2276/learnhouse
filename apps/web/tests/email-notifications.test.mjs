// Fork: email notifications, browser-side client.
import { afterEach, beforeEach, describe, expect, test } from "bun:test";

let calls;
let respond;
let events;
beforeEach(() => {
  calls = [];
  events = [];
  respond = () => jsonResponse(200, {});
  globalThis.window = {
    location: { origin: "https://learn.test" },
    dispatchEvent: (event) => events.push(event.type),
  };
  globalThis.fetch = async (url, init) => {
    calls.push({ url: String(url), init });
    return respond(String(url), init);
  };
});
afterEach(() => {
  delete globalThis.window;
  delete globalThis.fetch;
});

function jsonResponse(status, body) {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

const mod = await import("../services/notifications/emailNotifications.ts");

const prefs = {
  available: true,
  level: "replies",
  uses_default: false,
  default_level: "highlights",
  weekly_digest: false,
  digest_available: true,
  digest_weekday: 0,
  digest_hour: 8,
  timezone: "UTC",
  communities: [
    { community_uuid: "community_a", name: "General", muted: false },
    { community_uuid: "community_b", name: "Showcase", muted: true },
  ],
  labels: [
    { id: "general", name: "General", muted: false },
    { id: "idea", name: "Ideas", muted: true },
  ],
  assignments_available: true,
  assignment_updates: true,
  assignment_reminders: false,
  assignment_reminder_days: 3,
  can_manage: false,
};

describe("email notification client", () => {
  test("turns what the API returned into what it accepts", () => {
    expect(mod.toInput(prefs)).toEqual({
      level: "replies",
      weekly_digest: false,
      muted_community_uuids: ["community_b"],
      muted_labels: ["idea"],
      assignment_updates: true,
      assignment_reminders: false,
      assignment_reminder_days: 3,
    });
  });

  test("offers every level and reminder time the API knows", () => {
    expect([...mod.LEVELS].sort()).toEqual(["all", "highlights", "mentions", "off", "replies"]);
    expect(mod.LEVELS[0]).toBe("highlights");
    expect(mod.REMINDER_DAYS).toEqual([1, 2, 3, 7]);
  });

  test("reads and saves preferences with the session token", async () => {
    respond = () => jsonResponse(200, prefs);
    expect(await mod.getPreferences(4, "tok")).toEqual(prefs);
    await mod.savePreferences(4, mod.toInput(prefs), "tok");
    expect(calls[0].url.endsWith("email-notifications/org/4/preferences")).toBe(true);
    expect(calls[0].init.method).toBe("GET");
    expect(calls[1].init.method).toBe("PUT");
    expect(JSON.parse(calls[1].init.body).muted_labels).toEqual(["idea"]);
    expect(new Headers(calls[1].init.headers).get("authorization")).toBe("Bearer tok");
  });

  test("organization settings and the test email use their own routes", async () => {
    respond = (url) => jsonResponse(200, url.endsWith("/test") ? { sent_to: "a@b.c" } : {});
    await mod.getOrgSettings(4, "tok");
    await mod.saveOrgSettings(4, { enabled: true }, "tok");
    expect((await mod.sendTestEmail(4, "digest", "tok")).sent_to).toBe("a@b.c");
    expect(calls.map((c) => `${c.init.method} ${c.url.split("email-notifications")[1]}`)).toEqual([
      "GET /org/4/settings",
      "PUT /org/4/settings",
      "POST /org/4/test",
    ]);
    expect(JSON.parse(calls[2].init.body)).toEqual({ kind: "digest" });
  });

  test("errors carry the status and the server's message", async () => {
    respond = () => jsonResponse(429, { detail: { code: "RATE_LIMITED", message: "Too many test emails." } });
    const limited = await mod.sendTestEmail(4, "digest", "tok").catch((e) => e);
    expect(limited).toBeInstanceOf(mod.EmailNotificationsError);
    expect(limited.status).toBe(429);
    expect(limited.message).toBe("Too many test emails.");

    respond = () => jsonResponse(403, { detail: "Only organization admins" });
    expect((await mod.getOrgSettings(4, "tok").catch((e) => e)).message).toBe("Only organization admins");

    // Validation errors are a list; a body that is not JSON has no message at all.
    respond = () => jsonResponse(422, { detail: [{ msg: "bad" }] });
    expect((await mod.getOrgSettings(4, "tok").catch((e) => e)).message).toBe("");
    respond = () => new Response("<html>", { status: 502 });
    const down = await mod.getPreferences(4, "tok").catch((e) => e);
    expect(down.status).toBe(502);
    expect(down.message).toBe("");
  });

  test("opening the dialog is one browser event", () => {
    mod.openEmailNotifications();
    expect(events).toEqual([mod.OPEN_EMAIL_NOTIFICATIONS_EVENT]);
    delete globalThis.window;
    expect(() => mod.openEmailNotifications()).not.toThrow();
  });
});
