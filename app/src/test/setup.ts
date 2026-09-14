/** Vitest setup: jest-dom matchers, plus a default Tauri IPC mock so no test
 * accidentally depends on a real webview being present. */

import "@testing-library/jest-dom/vitest";
import { vi } from "vitest";

vi.mock("@tauri-apps/api/core", () => ({
  invoke: vi.fn().mockRejectedValue(new Error("no Tauri runtime in tests")),
}));
