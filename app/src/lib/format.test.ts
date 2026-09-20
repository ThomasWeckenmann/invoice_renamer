/** Tests for formatDecimalGB's rounding against the two real shipped models'
 * exact catalog byte totals. */

import { describe, expect, it } from "vitest";
import { formatDecimalGB } from "./format";

describe("formatDecimalGB", () => {
  it("rounds Qwen3 0.6B's total catalog size to 1.5 GB", () => {
    expect(formatDecimalGB(1_519_182_365)).toBe("1.5 GB");
  });

  it("rounds Granite 3.3 2B Instruct's total catalog size to 5.1 GB", () => {
    expect(formatDecimalGB(5_071_858_627)).toBe("5.1 GB");
  });

  it("uses decimal (10^9), not binary (2^30), gigabytes", () => {
    expect(formatDecimalGB(1_000_000_000)).toBe("1.0 GB");
  });
});
