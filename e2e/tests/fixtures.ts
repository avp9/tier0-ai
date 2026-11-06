import { test as base } from "@playwright/test";
import { Controls } from "../support/stack";

/** Every test starts from an empty ticket system, no scripted failures and fresh rate limits. */
export const test = base.extend<{ controls: Controls }>({
  controls: async ({}, use) => {
    const controls = await Controls.open();
    await controls.reset();
    await use(controls);
    await controls.dispose();
  },
});

export { expect } from "@playwright/test";
