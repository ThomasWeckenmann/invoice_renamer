/** Tests for the worker fetch wrapper: endpoint resolution, auth header, error normalization. */

import { invoke } from "@tauri-apps/api/core";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, apiFetch } from "./client";

const mockedInvoke = vi.mocked(invoke);

describe("apiFetch", () => {
  beforeEach(() => {
    mockedInvoke.mockResolvedValue({ port: 4321, token: "session-token" });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    mockedInvoke.mockReset();
  });

  it("resolves the worker endpoint and attaches the bearer token", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await apiFetch<{ ok: boolean }>("/models");

    expect(result).toEqual({ ok: true });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:4321/models",
      expect.objectContaining({ headers: expect.any(Headers) }),
    );
    const headers = fetchMock.mock.calls[0][1].headers as Headers;
    expect(headers.get("Authorization")).toBe("Bearer session-token");
  });

  it("throws an ApiError carrying the response's detail message", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: "model not installed" }), { status: 422 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const failure = await apiFetch("/analyses").catch((err: unknown) => err);

    expect(failure).toBeInstanceOf(ApiError);
    expect((failure as ApiError).status).toBe(422);
    expect((failure as ApiError).message).toBe("model not installed");
  });

  it("falls back to statusText when the error body isn't JSON", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("oops", { status: 500 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(apiFetch("/models")).rejects.toThrow(ApiError);
  });
});
