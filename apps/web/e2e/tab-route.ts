import { expect, type Page } from "@playwright/test";

/** Doorloop de sequentiële focusroute tot de browserfocus de pagina verlaat. */
export async function expectTabRoute(page: Page) {
  const tabLimit = 2 + await page.locator("a[href], button, input, select, textarea, summary, [tabindex], [contenteditable]").count();
  const stops = [];
  for (let i = 0; i < tabLimit; i++) {
    await page.keyboard.press("Tab");
    const stop = await page.evaluate(() => {
      const el = document.activeElement;
      if (!el || el === document.body) return null;

      const box = el.getBoundingClientRect();
      const style = getComputedStyle(el);
      // Toets ook hoge focusbare scrollregio's die gedeeltelijk in de viewport staan.
      const left = Math.max(0, box.left);
      const top = Math.max(0, box.top);
      const width = Math.min(innerWidth, box.right) - left;
      const height = Math.min(innerHeight, box.bottom) - top;
      const fractions = [0.1, 0.5, 0.9];
      const visible = width > 0 && height > 0 && fractions.some((y) => fractions.some((x) =>
        el.contains(document.elementFromPoint(left + width * x, top + height * y))));
      const ring = style.outlineStyle !== "none" && parseFloat(style.outlineWidth) >= 3;
      let contrast = 0;
      if (ring) {
        const canvas = document.createElement("canvas");
        canvas.width = canvas.height = 1;
        const ctx = canvas.getContext("2d")!;
        const paint = (color: string) => {
          ctx.fillStyle = color;
          ctx.fillRect(0, 0, 1, 1);
        };
        const luminance = () => [...ctx.getImageData(0, 0, 1, 1).data].slice(0, 3)
          .map((value) => value / 255)
          .map((value) => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4)
          .reduce((sum, value, i) => sum + value * [0.2126, 0.7152, 0.0722][i], 0);
        const backgrounds = [];
        for (let parent = el.parentElement; parent; parent = parent.parentElement) {
          backgrounds.push(getComputedStyle(parent).backgroundColor);
        }
        paint("#fff");
        backgrounds.reverse().forEach(paint);
        const surface = luminance();
        paint(style.outlineColor);
        const outline = luminance();
        contrast = (Math.max(outline, surface) + 0.05) / (Math.min(outline, surface) + 0.05);
      }
      return {
        name: (el.getAttribute("aria-label") || el.textContent || el.tagName).trim().slice(0, 60),
        testId: el.getAttribute("data-testid"),
        skipLink: el.classList.contains("skip-link"),
        ring,
        contrast,
        visible,
      };
    });
    if (!stop) {
      expect(stops.length, "route bevat focusstops na de skip-link").toBeGreaterThan(1);
      return stops;
    }
    const position = `Tab ${i + 1} bij ${stop.name}`;
    if (i === 0) expect(stop.skipLink, "eerste tabstop is de skip-link").toBe(true);
    expect(stop.ring, `Peil-ring bij ${position}`).toBe(true);
    expect(stop.contrast, `ringcontrast bij ${position}`).toBeGreaterThanOrEqual(3);
    expect(stop.visible, `onbedekte focus bij ${position}`).toBe(true);
    stops.push(stop);
  }
  throw new Error("De volledige Tab-route is niet doorlopen.");
}
