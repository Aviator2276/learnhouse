// Fork: Authentik sign-in, browser-side guards.
import { afterEach, beforeEach, describe, expect, mock, test } from "bun:test";

function memoryStorage() {
  const data = new Map();
  return {
    getItem: (k) => (data.has(k) ? data.get(k) : null),
    setItem: (k, v) => data.set(k, String(v)),
    removeItem: (k) => data.delete(k),
    clear: () => data.clear(),
  };
}

let assigned;
beforeEach(() => {
  assigned = null;
  globalThis.window = {
    sessionStorage: memoryStorage(),
    localStorage: memoryStorage(),
    location: { origin: "https://lh.test", assign: (u) => (assigned = u) },
  };
});
afterEach(() => {
  delete globalThis.window;
  delete globalThis.fetch;
});

const mod = await import("../services/auth/authentik.ts");

function jsonResponse(status, body) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

describe("safeNextPath", () => {
  test("keeps same-site relative paths", () => {
    expect(mod.safeNextPath("/courses?x=1")).toBe("/courses?x=1");
  });
  test("refuses every open-redirect shape", () => {
    for (const bad of ["https://evil.test", "//evil.test", "/\\evil.test", "evil", "", null, undefined, "/a\nb", "/a\u007f"]) {
      expect(mod.safeNextPath(bad)).toBe("/");
    }
  });
});

describe("startAuthentikLogin", () => {
  test("stores the binding for this tab only and goes to Authentik", async () => {
    globalThis.fetch = mock(async (url, init) => {
      expect(url).toBe("/api/auth/authentik/authorize");
      expect(JSON.parse(init.body)).toEqual({ org_id: 3, invite_code: null, next: "/" });
      return jsonResponse(200, { url: "https://auth.test/authorize?x=1", state: "st", binding: "bd" });
    });
    await mod.startAuthentikLogin({ orgId: 3, next: "https://evil.test/" });
    expect(window.sessionStorage.getItem("lh_authentik_binding")).toBe("bd");
    expect(window.sessionStorage.getItem("lh_authentik_state")).toBe("st");
    expect(assigned).toBe("https://auth.test/authorize?x=1");
  });

  test("never navigates to a javascript: URL", async () => {
    globalThis.fetch = mock(async () => jsonResponse(200, { url: "javascript:alert(1)", state: "s", binding: "b" }));
    const result = await mod.startAuthentikLogin({});
    expect(result.ok).toBe(false);
    expect(assigned).toBeNull();
  });

  test("surfaces the API's refusal", async () => {
    globalThis.fetch = mock(async () => jsonResponse(403, { detail: { message: "Not allowed here" } }));
    expect(await mod.startAuthentikLogin({})).toEqual({ ok: false, error: "Not allowed here" });
  });
});

describe("completeAuthentikLogin", () => {
  test("refuses a callback this tab did not start (login CSRF)", async () => {
    globalThis.fetch = mock(async () => jsonResponse(200, {}));
    await expect(mod.completeAuthentikLogin(new URLSearchParams("code=c&state=s"))).rejects.toThrow(/not started/);
    expect(globalThis.fetch).not.toHaveBeenCalled();
  });

  test("refuses a mismatched state and burns the binding", async () => {
    window.sessionStorage.setItem("lh_authentik_binding", "bd");
    window.sessionStorage.setItem("lh_authentik_state", "expected");
    globalThis.fetch = mock(async () => jsonResponse(200, {}));
    await expect(mod.completeAuthentikLogin(new URLSearchParams("code=c&state=other"))).rejects.toThrow();
    expect(window.sessionStorage.getItem("lh_authentik_binding")).toBeNull();
  });

  test("reports IdP errors without echoing their description", async () => {
    await expect(
      mod.completeAuthentikLogin(new URLSearchParams("error=access_denied&error_description=<script>")),
    ).rejects.toThrow("Sign-in was cancelled or denied.");
  });

  test("posts code, state, binding and iss; sanitises callback_url", async () => {
    window.sessionStorage.setItem("lh_authentik_binding", "bd");
    window.sessionStorage.setItem("lh_authentik_state", "st");
    globalThis.fetch = mock(async (url, init) => {
      expect(url).toBe("/api/auth/authentik/callback");
      expect(JSON.parse(init.body)).toEqual({ code: "c", state: "st", binding: "bd", iss: "https://auth.test/" });
      return jsonResponse(200, { tokens: { access_token: "a" }, callback_url: "//evil.test" });
    });
    const data = await mod.completeAuthentikLogin(new URLSearchParams("code=c&state=st&iss=https://auth.test/"));
    expect(data.callback_url).toBe("/");
    expect(window.localStorage.getItem("lh_authentik_sso")).toBe("1");
  });
});

describe("consumeAuthentikLogoutUrl", () => {
  test("only for Authentik sessions, and only once", async () => {
    globalThis.fetch = mock(async () => jsonResponse(200, { url: "https://auth.test/end-session/?client_id=x" }));
    expect(await mod.consumeAuthentikLogoutUrl()).toBeNull();
    expect(globalThis.fetch).not.toHaveBeenCalled();

    mod.markAuthentikSession();
    expect(await mod.consumeAuthentikLogoutUrl()).toBe("https://auth.test/end-session/?client_id=x");
    expect(await mod.consumeAuthentikLogoutUrl()).toBeNull();
  });
});
