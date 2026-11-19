import type { FullConfig, Suite, TestCase, TestResult } from "@playwright/test/reporter";
import { afterEach, expect, it, vi } from "vitest";

import StrictReporter from "../../e2e/strict-reporter";

afterEach(() => {
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

it("keurt een overgeslagen test af als de strikte run verder slaagt", async () => {
  vi.stubEnv("MSO_E2E_STRICT", "1");
  vi.stubEnv("MSO_E2E_MIN_TESTS", "1");
  const error = vi.spyOn(console, "error").mockImplementation(() => {});
  const testCase = { titlePath: () => ["testscherm"], annotations: [] } as unknown as TestCase;
  const suite = { allTests: () => [testCase] } as unknown as Suite;
  const reporter = new StrictReporter();
  reporter.onBegin({} as FullConfig, suite);
  reporter.onTestEnd(testCase, { status: "skipped", annotations: [{ type: "skip", description: "proef zonder sessie" }] } as TestResult);

  await expect(reporter.onEnd({ status: "passed", startTime: new Date(), duration: 0 })).resolves.toEqual({ status: "failed" });
  expect(error).toHaveBeenCalledWith(expect.stringContaining("testscherm — proef zonder sessie"));
});
