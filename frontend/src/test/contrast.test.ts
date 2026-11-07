import css from "../styles.css?raw";

function parseHex(hex: string): [number, number, number] {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function luminance(hex: string): number {
  const [r, g, b] = parseHex(hex).map((channel) => {
    const c = channel / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  }) as [number, number, number];
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function ratio(a: string, b: string): number {
  const [light, dark] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number];
  return (light + 0.05) / (dark + 0.05);
}

function tokens(block: string): Record<string, string> {
  return Object.fromEntries(
    [...block.matchAll(/--([\w-]+):\s*(#[0-9a-fA-F]{6})\s*;/g)].map((m) => [m[1], m[2]]),
  ) as Record<string, string>;
}

const darkStart = css.indexOf("@media (prefers-color-scheme: dark)");
const themes = {
  light: tokens(css.slice(css.indexOf(":root"), darkStart)),
  dark: { ...tokens(css.slice(css.indexOf(":root"), darkStart)), ...tokens(css.slice(darkStart)) },
};

// [foreground, background, minimum ratio]. 4.5 is WCAG AA for normal text, 3 for UI boundaries.
const TEXT: [string, string][] = [
  ["text", "surface"],
  ["text", "bg"],
  ["muted", "surface"],
  ["muted", "bg"],
  ["on-primary", "primary"],
  ["text", "row-active"],
  ["muted", "row-active"],
  ["text", "row-hover"],
  ["error-text", "error-bg"],
  ["notice-text", "notice-bg"],
  ["link", "surface"],
];
const COMPONENTS: [string, string][] = [
  ["border", "surface"],
  ["border", "bg"],
  ["focus", "surface"],
  ["focus", "bg"],
  ["primary", "surface"],
];

function colour(theme: Record<string, string>, name: string): string {
  const value = theme[name];
  if (value === undefined) throw new Error(`--${name} is not defined`);
  return value;
}

describe.each(Object.entries(themes))("%s theme colour contrast", (_name, theme) => {
  it("defines every token the pairs below use", () => {
    for (const [a, b] of [...TEXT, ...COMPONENTS]) {
      expect(theme[a], `--${a}`).toBeDefined();
      expect(theme[b], `--${b}`).toBeDefined();
    }
  });

  it.each(TEXT)("text --%s on --%s meets 4.5:1", (fg, bg) => {
    expect(ratio(colour(theme, fg), colour(theme, bg))).toBeGreaterThanOrEqual(4.5);
  });

  it.each(COMPONENTS)("boundary --%s against --%s meets 3:1", (fg, bg) => {
    expect(ratio(colour(theme, fg), colour(theme, bg))).toBeGreaterThanOrEqual(3);
  });
});

// The page background is a gradient: text has to read on every colour it passes through.
function gradientStops(block: string): string[] {
  const gradient = /--bg-grad:\s*linear-gradient\(([^)]*)\)/.exec(block);
  return [...(gradient?.[1] ?? "").matchAll(/#[0-9a-fA-F]{6}/g)].map((m) => m[0]);
}

const gradients = {
  light: gradientStops(css.slice(css.indexOf(":root"), darkStart)),
  dark: gradientStops(css.slice(darkStart)),
};

describe.each(Object.entries(themes))("%s theme page gradient", (name, theme) => {
  const stops = gradients[name as keyof typeof gradients];

  it("has colour stops to check", () => {
    expect(stops.length).toBeGreaterThanOrEqual(2);
  });

  it.each(["text", "muted"])("--%s is readable on every stop", (fg) => {
    for (const stop of stops) {
      expect(ratio(colour(theme, fg), stop), `${fg} on ${stop}`).toBeGreaterThanOrEqual(4.5);
    }
  });
});

describe("the contrast calculator", () => {
  it("matches known values", () => {
    expect(ratio("#000000", "#ffffff")).toBeCloseTo(21, 0);
    expect(ratio("#777777", "#ffffff")).toBeCloseTo(4.48, 1);
    expect(ratio("#ffffff", "#ffffff")).toBe(1);
  });
});
