export function safeNext(next: string | null): string {
  if (
    !next ||
    next.length > 256 ||
    !/^\/(?![/\\])/.test(next) ||
    /[^!-~]/.test(next) ||
    next.includes("://") ||
    next.includes("\\")
  ) {
    return "/";
  }

  let [pathname] = next.split(/[?#]/, 1);
  const target = new URL("https://mso.invalid");
  try {
    while (true) {
      target.pathname = pathname;
      if (
        pathname.includes("\\") ||
        pathname.includes("//") ||
        target.pathname.includes("//") ||
        /^\/login/i.test(pathname) ||
        /^\/login/i.test(target.pathname)
      ) {
        return "/";
      }
      // Controleer elke gedecodeerde padnaam. Behoud het oorspronkelijke terugkeerpad bij succes.
      if (!pathname.includes("%")) {
        return next;
      }
      pathname = decodeURIComponent(pathname);
    }
  } catch {
    return "/";
  }
}
