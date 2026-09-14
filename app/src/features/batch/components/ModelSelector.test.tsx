/** Tests for the model selector's error-recovery affordance. */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ModelSelector } from "./ModelSelector";

describe("ModelSelector", () => {
  it("shows a retry control on error and wires it to onRefresh", () => {
    const onRefresh = vi.fn();
    render(
      <ModelSelector
        models={[]}
        loading={false}
        error="worker unreachable"
        selectedModelId={null}
        onSelect={() => {}}
        onDownload={() => {}}
        onRemove={() => {}}
        onRefresh={onRefresh}
      />,
    );

    expect(screen.getByRole("alert")).toHaveTextContent("worker unreachable");
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(onRefresh).toHaveBeenCalledTimes(1);
  });
});
