// Fork: plugNmeet live classes, browser-side client.
import { afterEach, beforeEach, describe, expect, test } from "bun:test";

let assigned;
let calls;
let respond;
beforeEach(() => {
  assigned = null;
  calls = [];
  respond = () => jsonResponse(200, {});
  globalThis.window = { location: { origin: "https://learn.test", assign: (u) => (assigned = u) } };
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

const mod = await import("../services/plugnmeet/liveClasses.ts");

describe("isSafeJoinUrl", () => {
  test("accepts https meeting links with a token", () => {
    expect(mod.isSafeJoinUrl("https://meet.example.com/?access_token=abc")).toBe(true);
  });
  test.each([
    "http://meet.example.com/?access_token=abc",
    "javascript:alert(1)//?access_token=x",
    "https://meet.example.com/",
    "not a url",
  ])("rejects %s", (url) => {
    expect(mod.isSafeJoinUrl(url)).toBe(false);
  });
});

describe("joining", () => {
  test("follows the join URL with the session token sent as a bearer header", async () => {
    respond = () => jsonResponse(200, { join_url: "https://meet.example.com/?access_token=t1" });
    await mod.joinLiveClass("liveclass_abc", "session-token");
    expect(assigned).toBe("https://meet.example.com/?access_token=t1");
    expect(calls[0].url).toEndWith("plugnmeet/classes/abc/join");
    expect(calls[0].init.method).toBe("POST");
    expect(calls[0].init.headers.get("Authorization")).toBe("Bearer session-token");
  });

  test("never follows a link to anything but https", async () => {
    respond = () => jsonResponse(200, { join_url: "http://evil.example/?access_token=t1" });
    await expect(mod.joinLiveClass("abc", "t")).rejects.toMatchObject({ code: "UNAVAILABLE" });
    expect(assigned).toBe(null);
  });

  test("keeps the server's error code, so an outage is not shown as 'not started'", async () => {
    respond = () => jsonResponse(409, { detail: { code: "NOT_STARTED", message: "This class hasn't started yet." } });
    await expect(mod.joinLiveClass("abc", "t")).rejects.toMatchObject({ status: 409, code: "NOT_STARTED" });
    respond = () => jsonResponse(503, { detail: { code: "UNAVAILABLE", message: "down" } });
    await expect(mod.joinQuickRoom(1, "t")).rejects.toMatchObject({ status: 503, code: "UNAVAILABLE" });
    respond = () => new Response("<html>bad gateway</html>", { status: 502 });
    await expect(mod.joinLiveClass("abc", "t")).rejects.toMatchObject({ status: 502, code: null });
    respond = () => jsonResponse(404, { detail: "Live class not found" });
    await expect(mod.getLiveClass("abc", "t")).rejects.toMatchObject({ status: 404, message: "Live class not found" });
  });
});

describe("requests", () => {
  test("list, schedule and scoped changes hit the right endpoints", async () => {
    respond = (url, init) => (init.method === "DELETE" ? new Response(null, { status: 204 }) : jsonResponse(200, []));
    await mod.listLiveClasses(3, "upcoming", "t", "course_x", 5);
    await mod.createLiveClasses(3, { title: "T", description: "", starts_at: "2030-01-01T10:00:00.000Z", duration_minutes: 60, timezone: "UTC" }, "t");
    await mod.updateLiveClass("liveclass_a b", { title: "U" }, "following", "t");
    await mod.cancelLiveClass("a", "this", "t");
    expect(await mod.deleteLiveClass("a", "following", "t")).toBeUndefined();
    const urls = calls.map((c) => `${c.init.method} ${c.url.replace(/^.*plugnmeet/, "")}`);
    expect(urls).toEqual([
      "GET /org/3/classes?when=upcoming&limit=5&course_uuid=course_x",
      "POST /org/3/classes",
      "PATCH /classes/a%20b?scope=following",
      "POST /classes/a/cancel?scope=this",
      "DELETE /classes/a?scope=following",
    ]);
    expect(JSON.parse(calls[1].init.body).title).toBe("T");
  });

  test("config falls back to disabled when the API is unreachable", async () => {
    globalThis.fetch = async () => {
      throw new TypeError("network");
    };
    expect(await mod.getLiveClassesConfig()).toEqual({ enabled: false, quick_room: false, attendance: false });
  });

  test("browserTimezone is an IANA zone", () => {
    expect(typeof mod.browserTimezone()).toBe("string");
    expect(mod.browserTimezone().length).toBeGreaterThan(0);
  });
});
