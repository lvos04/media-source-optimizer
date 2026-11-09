import type { FullConfig, FullResult, Reporter, Suite, TestCase, TestResult } from "@playwright/test/reporter";

/**
 * Strict mode for `make e2e` / CI (MSO_E2E_STRICT=1): a skipped test is a failure, not a silent pass.
 * Without credentials or test data the specs skip themselves (test.skip); in strict mode that fails the run, so a
 * green job really means every test ran. MSO_E2E_MIN_TESTS: lower bound for the number of tests found.
 * Without MSO_E2E_STRICT nothing changes (handy for running a single spec locally).
 */
export default class StrictReporter implements Reporter {
  private readonly strict = process.env.MSO_E2E_STRICT === "1";
  private readonly minTests = Number.parseInt(process.env.MSO_E2E_MIN_TESTS ?? "0", 10) || 0;
  private total = 0;
  private readonly skipped: string[] = [];

  onBegin(_config: FullConfig, suite: Suite): void {
    this.total = suite.allTests().length;
  }

  onTestEnd(test: TestCase, result: TestResult): void {
    if (result.status !== "skipped") return;
    const reason = [...test.annotations, ...result.annotations].find((a) => a.type === "skip")?.description;
    this.skipped.push(`${test.titlePath().filter(Boolean).join(" › ")}${reason ? ` — ${reason}` : ""}`);
  }

  // the returned status overrides the run result (and so the exit code); Reporter.onEnd must return a promise for that
  async onEnd(result: FullResult): Promise<{ status?: FullResult["status"] } | undefined> {
    if (!this.strict) return undefined;
    const problems: string[] = [];
    if (this.skipped.length) {
      problems.push(`${this.skipped.length} test(s) overgeslagen — in strikte modus telt dat als fout:`);
      problems.push(...this.skipped.map((s) => `  - ${s}`));
    }
    if (this.total < this.minTests) problems.push(`maar ${this.total} test(s) gevonden, minimaal ${this.minTests} verwacht`);
    if (problems.length) {
      console.error(`\nStrikte E2E mislukt:\n${problems.join("\n")}`);
      return { status: "failed" };
    }
    console.log(`\nStrikte E2E: ${this.total} test(s) gevonden, 0 overgeslagen (uitslag: ${result.status})`);
    return undefined;
  }
}
