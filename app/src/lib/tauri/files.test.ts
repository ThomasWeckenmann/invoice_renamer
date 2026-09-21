/** Tests for path-to-File conversion: basename extraction and byte reading. */

import { invoke } from "@tauri-apps/api/core";
import { afterEach, describe, expect, it, vi } from "vitest";
import { basename, readPathAsFile } from "./files";

const mockedInvoke = vi.mocked(invoke);

describe("basename", () => {
  it("extracts the last path segment for unix and windows separators", () => {
    expect(basename("/invoices/a.pdf")).toBe("a.pdf");
    expect(basename("C:\\invoices\\a.pdf")).toBe("a.pdf");
  });

  it("ignores a trailing separator", () => {
    expect(basename("/invoices/a.pdf/")).toBe("a.pdf");
  });
});

describe("readPathAsFile", () => {
  afterEach(() => {
    mockedInvoke.mockReset();
  });

  it("reads bytes via the Tauri command and builds a PDF File named after the path", async () => {
    mockedInvoke.mockResolvedValue([0x25, 0x50, 0x44, 0x46]);

    const file = await readPathAsFile("/invoices/a.pdf");

    expect(mockedInvoke).toHaveBeenCalledWith("read_file_bytes", { path: "/invoices/a.pdf" });
    expect(file.name).toBe("a.pdf");
    expect(file.type).toBe("application/pdf");
    expect(file.size).toBe(4);
  });

  it("builds a JPEG File for a .jpg path", async () => {
    mockedInvoke.mockResolvedValue([0xff, 0xd8, 0xff]);

    const file = await readPathAsFile("/invoices/scan.jpg");

    expect(file.name).toBe("scan.jpg");
    expect(file.type).toBe("image/jpeg");
  });

  it("builds a JPEG File for a .jpeg path", async () => {
    mockedInvoke.mockResolvedValue([0xff, 0xd8, 0xff]);

    const file = await readPathAsFile("/invoices/scan.jpeg");

    expect(file.type).toBe("image/jpeg");
  });
});
