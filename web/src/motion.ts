// The app's four motion moments, using the Web Animations API.
// 1 marker draw + tab slide (Reader), 2 waiting steps (Progress),
// 3 scroll + one pulse (Reader), 4 the reply assembling (DraftDialog).
// With prefers-reduced-motion every moment jumps to its final state.

export const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

const EASE = "cubic-bezier(0.3, 0.7, 0.2, 1)";
/** Time one finding's marker takes to cross all its lines. */
export const MARK_MS = 400;
/** Gap between findings starting. */
export const MARK_STAGGER = 520;

/**
 * Moment 1. `root` holds `[data-line]` rects and one `[data-tab]` (and `[data-bracket]`).
 * Lines draw left to right in turn, then the tab slides out from the page edge.
 */
export function drawMarks(root: HTMLElement, delay: number): Animation[] {
  if (reducedMotion()) return [];
  const lines = [...root.querySelectorAll<HTMLElement>("[data-line]")];
  const per = MARK_MS / Math.max(1, lines.length);
  const out: Animation[] = [];
  lines.forEach((el, i) => {
    out.push(
      el.animate([{ transform: "scaleX(0)" }, { transform: "scaleX(1)" }], {
        duration: per + 60,
        delay: delay + i * per,
        easing: i === lines.length - 1 ? EASE : "linear",
        fill: "backwards",
      }),
    );
  });
  const after = delay + MARK_MS + 40;
  const bracket = root.querySelector<HTMLElement>("[data-bracket]");
  if (bracket)
    out.push(bracket.animate([{ transform: "scaleY(0)" }, { transform: "scaleY(1)" }], { duration: 220, delay: after, easing: EASE, fill: "backwards" }));
  const tab = root.querySelector<HTMLElement>("[data-tab]");
  if (tab)
    out.push(
      tab.animate(
        [
          { transform: "translateX(-14px)", opacity: 0 },
          { transform: "none", opacity: 1 },
        ],
        { duration: 260, delay: after + 80, easing: EASE, fill: "backwards" },
      ),
    );
  return out;
}

/** Moment 3: one soft ring around the quote. */
export function pulse(el: HTMLElement, ring: string): void {
  if (reducedMotion()) return;
  el.animate(
    [
      { boxShadow: `0 0 0 0 ${ring}` },
      { boxShadow: `0 0 0 8px ${ring}`, offset: 0.35 },
      { boxShadow: "0 0 0 14px transparent" },
    ],
    { duration: 900, easing: "ease-out" },
  );
}

/** Moments 2 and 4: an element settles into place. */
export function settle(el: HTMLElement, delay = 0, distance = 6): Animation | null {
  if (reducedMotion()) return null;
  return el.animate(
    [
      { opacity: 0, transform: `translateY(${distance}px)` },
      { opacity: 1, transform: "none" },
    ],
    { duration: 320, delay, easing: EASE, fill: "backwards" },
  );
}
