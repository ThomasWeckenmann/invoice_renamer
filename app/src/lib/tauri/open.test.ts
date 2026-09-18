/** Tests that openWithSystemDefault invokes the right Tauri command name
 * and argument shape, since a mismatch with the Rust side fails silently
 * at runtime rather than at compile time. */

import { invoke } from "@tauri-apps/api/core";
import { afterEach, describe, expect, it, vi } from "vitest";
import { openWithSystemDefault } from "./open";

const mockedInvoke = vi.mocked(invoke);

describe("openWithSystemDefault", () => {
  afterEach(() => {
    mockedInvoke.mockReset();
  });

  it("invokes open_with_system_default with the path", async () => {
    mockedInvoke.mockResolvedValue(undefined);

    await openWithSystemDefault("/invoices/a.pdf");

    expect(mockedInvoke).toHaveBeenCalledWith("open_with_system_default", {
      path: "/invoices/a.pdf",
    });
  });
});
