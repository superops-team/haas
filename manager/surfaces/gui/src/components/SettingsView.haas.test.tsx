import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SettingsView } from "./SettingsView";

type FetchCall = { url: string; method: string; body?: unknown };

const haasSettings = {
  ok: true,
  enabled: true,
  base_url: "http://127.0.0.1:8092",
  user_id: "manager",
  harness_id: "chrn_codex_default",
  harness_base: "codex",
  image: "haas:local",
  image_digest_configured: true,
  image_digest: "sha256:abc123",
  strategy: "deterministic",
  require_trusted_workspace: true,
  agent_allowlist: ["code"],
  trigger_keywords: ["haas", "delegate"],
  idle_ttl_seconds: 1800,
  max_container_lifetime_seconds: 28800,
  request_timeout_seconds: 30,
  local_autostart: true,
  network_access: true,
  workspace_mode: "workspace-write",
  approval_mode: "on-request",
  policy_defaults_revision: 1,
  allow_unpinned_local_image: false,
  local_status: {
    enabled: true,
    status: "running",
    running: true,
    managed: true,
    pid: 123,
    url: "http://127.0.0.1:8092",
    reason: null,
  },
  has_api_token: true,
};

function stubFetch() {
  const calls: FetchCall[] = [];
  const fn = vi.fn(async (url: string, init?: RequestInit) => {
    const method = (init?.method || "GET").toUpperCase();
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ url, method, body });

    if (url.includes("/v1/settings/haas-delegation")) {
      if (method === "POST") {
        return {
          ok: true,
          json: async () => ({
            ...haasSettings,
            ...(body && typeof body === "object" ? body : {}),
            has_api_token: !(body as { clear_api_token?: boolean } | undefined)?.clear_api_token,
          }),
        } as Response;
      }
      return { ok: true, json: async () => haasSettings } as Response;
    }

    return { ok: true, json: async () => ({}) } as Response;
  });
  vi.stubGlobal("fetch", fn);
  return calls;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("SettingsView HaaS delegation settings", () => {
  it("renders saved HaaS settings without exposing the token", async () => {
    stubFetch();
    render(<SettingsView initialTab="execution" />);

    expect(await screen.findByTestId("haas-delegation-card")).toBeTruthy();
    expect(screen.getByTestId("haas-base-url")).toHaveProperty("value", "http://127.0.0.1:8092");
    expect(screen.getByTestId("haas-image")).toHaveProperty("value", "haas:local");
    expect(screen.getByTestId("haas-api-token")).toHaveProperty("value", "");
    expect(screen.getByTestId("haas-local-status").textContent).toContain("running");
    expect(screen.getByPlaceholderText("Token saved")).toBeTruthy();
    expect(document.body.textContent).not.toContain("dev-token");
  });

  it("saves edited non-secret settings and writes token only when entered", async () => {
    const calls = stubFetch();
    render(<SettingsView initialTab="execution" />);

    const baseUrl = await screen.findByTestId("haas-base-url");
    fireEvent.change(baseUrl, { target: { value: "http://127.0.0.1:8099" } });
    fireEvent.change(screen.getByTestId("haas-api-token"), { target: { value: "new-secret-token" } });
    fireEvent.change(screen.getByTestId("haas-trigger-keywords"), {
      target: { value: "haas, remote run,delegate" },
    });
    fireEvent.click(screen.getByTestId("haas-local-autostart"));
    fireEvent.click(screen.getByTestId("haas-network-access"));
    fireEvent.change(screen.getByTestId("haas-workspace-mode"), {
      target: { value: "read-only" },
    });
    fireEvent.click(screen.getByTestId("haas-save"));

    await waitFor(() => {
      const post = calls.find((call) => call.method === "POST");
      expect(post?.url).toContain("/v1/settings/haas-delegation");
      expect(post?.body).toMatchObject({
        base_url: "http://127.0.0.1:8099",
        api_token: "new-secret-token",
        local_autostart: false,
        network_access: false,
        workspace_mode: "read-only",
        approval_mode: "on-request",
        trigger_keywords: ["haas", "remote run", "delegate"],
      });
    });
    expect(screen.getByTestId("haas-api-token")).toHaveProperty("value", "");
  });

  it("clears the stored HaaS token without sending other secret material", async () => {
    const calls = stubFetch();
    render(<SettingsView initialTab="execution" />);

    fireEvent.click(await screen.findByTestId("haas-clear-token"));

    await waitFor(() => {
      const post = calls.find((call) => call.method === "POST");
      expect(post?.body).toEqual({ clear_api_token: true });
    });
  });

  it("shows a local error when the settings update request fails", async () => {
    const fn = vi.fn(async (url: string, init?: RequestInit) => {
      const method = (init?.method || "GET").toUpperCase();
      if (url.includes("/v1/settings/haas-delegation") && method === "POST") {
        throw new Error("offline");
      }
      return { ok: true, json: async () => haasSettings } as Response;
    });
    vi.stubGlobal("fetch", fn);
    render(<SettingsView initialTab="execution" />);

    fireEvent.click(await screen.findByTestId("haas-save"));

    expect(await screen.findByText("Couldn't save.")).toBeTruthy();
  });
});
