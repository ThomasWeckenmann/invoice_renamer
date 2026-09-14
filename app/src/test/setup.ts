/** Vitest setup: jest-dom matchers, DOM cleanup between tests (vitest
 * doesn't expose `afterEach` globally, so testing-library's own auto-cleanup
 * never registers unless done explicitly here), plus a default Tauri IPC
 * mock so no test accidentally depends on a real webview being present. */

import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

afterEach(() => {
  cleanup();
});

vi.mock("@tauri-apps/api/core", () => ({
  invoke: vi.fn().mockRejectedValue(new Error("no Tauri runtime in tests")),
}));

// ImportDropzone subscribes to window-level drag-drop on mount in every
// test that renders it (directly or via BatchWorkspace/App), so this needs
// a safe default even when a test isn't exercising drag-and-drop itself.
vi.mock("@tauri-apps/api/webview", () => ({
  getCurrentWebview: vi.fn(() => ({
    onDragDropEvent: vi.fn().mockResolvedValue(() => {}),
  })),
}));
