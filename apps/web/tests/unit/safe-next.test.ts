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

  it.each([
    "/%6Cogin",
    "/l%6fgin",
    "/lo%67in",
    "/log%69n",
    "/logi%6E",
    "/LoGiN",
    "/%4C%4F%47%49%4E",
    "/l%6Fgin-extra?x=1#deel",
    "/map/%2e%2E/%6Cogin",
    "/map%2f..%2Flogin",
    "/%256Cogin",
    "/%25256Cogin",
    "/map%252f%252e%252e%252f%256Cogin",
    "/l%6Fgin/..",
    "/%2fkosten",
    "/map%255Citem",
    "/kosten%",
    "/kosten%2",
    "/kosten%GG",
    "/kosten%FF",
    "/kosten%25GG",
    "/map%GG/../kosten",
  ])("weigert de routenaam %s na decodering of normalisatie", (value) => {
    expect(safeNext(value)).toBe("/");
  });

  it("behoudt dubbele slashes in de query en het fragment", () => {
    const value = "/kosten?x=a//b#c//d";
    expect(safeNext(value)).toBe(value);
  });

  it.each([
    "/kosten?x=1",
    "/k%6Fsten?x=a%2Fb&x=2#deel",
    "/map%2Fitem",
    "/kosten?x=%GG#%",
    "/" + "a".repeat(255),
    "/setup",
  ])("behoudt %s", (value) => {
    expect(safeNext(value)).toBe(value);
  });
});
