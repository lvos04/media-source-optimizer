import { describe, expect, it } from "vitest";
import { safeNext } from "@/lib/safe-next";

describe("safeNext", () => {
  it.each([null, "", "//evil.example", "/\\evil", "https://evil.example", "/a://b", "/kosten x", "/a\n", "/é", "/" + "a".repeat(256), "/login", "/login?x=1"]) ("weigert %s", (value) => {
    expect(safeNext(value)).toBe("/");
  });

  it.each([
    "/./login",
    "/%2e/login",
    "/login/..",
    "//",
    "/map//item",
    "/.//item",
    "/map//../item",
    "/map\\item",
  ])("valt terug op / voor %s", (value) => {
    expect(safeNext(value)).toBe("/");
  });

  it("behoudt dubbele slashes in de query en het fragment", () => {
    const value = "/kosten?x=a//b#c//d";
    expect(safeNext(value)).toBe(value);
  });

  it.each(["/kosten?x=1", "/" + "a".repeat(255), "/setup"]) ("behoudt %s", (value) => {
    expect(safeNext(value)).toBe(value);
  });
});
