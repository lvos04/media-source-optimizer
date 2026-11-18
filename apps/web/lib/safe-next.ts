export function safeNext(next: string | null): string {
  if (!next || next.length > 256 || !/^\/(?![/\\])/.test(next) || /[^!-~]/.test(next) ||
      next.includes("://") || next.includes("\\") || next.startsWith("/login")) {
    return "/";
  }
  return next;
}
