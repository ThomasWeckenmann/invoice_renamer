/** Smoke test for the root component's worker-startup gate. */

import { invoke } from "@tauri-apps/api/core";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";

const mockedInvoke = vi.mocked(invoke);

/** Answers `get_worker_status` with `status`; every other command rejects, as
 * the default test mock does. */
function mockWorkerStatus(status: Record<string, unknown>) {
  mockedInvoke.mockImplementation((command: string) =>
    command === "get_worker_status"
      ? Promise.resolve(status)
      : Promise.reject(new Error("no Tauri runtime in tests")),
  );
}

describe("App", () => {
  afterEach(() => {
    mockedInvoke.mockReset();
  });

  it("renders the batch workspace once the worker is ready", async () => {
    mockWorkerStatus({ state: "ready" });
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Import" })).toBeInTheDocument();
  });

  it("shows a loading state instead of the workspace while the worker starts", () => {
    mockWorkerStatus({ state: "starting" });
    render(<App />);

    expect(screen.getByRole("status")).toHaveTextContent("Starting the local worker");
    expect(screen.queryByRole("heading", { name: "Import" })).not.toBeInTheDocument();
  });

  it("swaps the loading state for the workspace once a later poll reports ready", async () => {
    let polls = 0;
    mockedInvoke.mockImplementation((command: string) => {
      if (command !== "get_worker_status") {
        return Promise.reject(new Error("no Tauri runtime in tests"));
      }
      polls += 1;
      return Promise.resolve(polls === 1 ? { state: "starting" } : { state: "ready" });
    });
    render(<App />);

    expect(screen.getByRole("status")).toHaveTextContent("Starting the local worker");

    expect(await screen.findByRole("heading", { name: "Import" })).toBeInTheDocument();
    expect(screen.queryByText(/Starting the local worker/)).not.toBeInTheDocument();
    expect(polls).toBeGreaterThan(1);
  });

  it("reports the reason when the worker never comes up", async () => {
    mockWorkerStatus({ state: "failed", message: "worker exited before becoming ready" });
    render(<App />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "worker exited before becoming ready",
    );
  });
});
