/** Smoke test for the root component. */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { App } from "./App";

describe("App", () => {
  it("renders the app heading", async () => {
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Invoice Renamer" })).toBeInTheDocument();
  });
});
