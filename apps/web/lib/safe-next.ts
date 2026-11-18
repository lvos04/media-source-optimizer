export function safeNext(next: string | null): string {
  if (
    !next ||
    next.length > 256 ||
    !/^\/(?![/\\])/.test(next) ||
    /[^!-~]/.test(next) ||
    next.includes("://") ||
    next.includes("\\") ||
    next.startsWith("/login")
  ) {
    return "/";
  }

  const [rawPath] = next.split(/[?#]/, 1);
  const { pathname } = new URL(next, "https://mso.invalid");
  if (rawPath.includes("//") || pathname.includes("//") || pathname.startsWith("/login")) {
    return "/";
  }
  return next;
}
