import "@testing-library/jest-dom/vitest";

beforeEach(() => {
  sessionStorage.clear();
  // jsdom does not implement scrolling.
  Element.prototype.scrollIntoView = vi.fn();
});
