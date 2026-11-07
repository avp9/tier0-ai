import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Avatar } from "./Avatar";

const alice = { email: "alice@buzztech.example", display_name: "Alice Demo", gender: "female" } as const;

describe("Avatar", () => {
  it("is a person icon, not the name, and keeps the name for screen readers", () => {
    render(<Avatar me={alice} />);
    const icon = screen.getByRole("button", { name: "Signed in as Alice Demo" });
    expect(icon.querySelector("svg")).not.toBeNull();
    expect(icon).toHaveClass("avatar-female");
  });

  it("describes itself with the signed-in email, shown on hover or focus", async () => {
    render(<Avatar me={alice} />);
    const icon = screen.getByRole("button", { name: "Signed in as Alice Demo" });
    const tip = screen.getByRole("tooltip");
    expect(tip).toHaveTextContent("alice@buzztech.example");
    expect(icon).toHaveAttribute("aria-describedby", tip.id);
    await userEvent.setup().tab();
    expect(icon).toHaveFocus(); // keyboard users can reach it too
  });

  it("uses a male, female or neutral icon depending on the account", () => {
    const { rerender } = render(<Avatar me={{ ...alice, gender: "male" }} />);
    expect(screen.getByRole("button")).toHaveClass("avatar-male");
    rerender(<Avatar me={{ ...alice, gender: null }} />);
    expect(screen.getByRole("button")).toHaveClass("avatar-neutral");
    rerender(<Avatar me={{ email: alice.email, display_name: alice.display_name }} />);
    expect(screen.getByRole("button")).toHaveClass("avatar-neutral");
  });

  it("draws a different figure for male, female and neutral", () => {
    const figure = (gender: "male" | "female" | null) => {
      const { container, unmount } = render(<Avatar me={{ ...alice, gender }} />);
      const svg = container.querySelector("svg");
      const drawing = svg?.innerHTML;
      const kind = svg?.getAttribute("data-figure");
      unmount();
      return { kind, drawing };
    };
    const [male, female, neutral] = [figure("male"), figure("female"), figure(null)];
    expect([male.kind, female.kind, neutral.kind]).toEqual(["male", "female", "neutral"]);
    expect(new Set([male.drawing, female.drawing, neutral.drawing]).size).toBe(3);
  });

  it("renders the email as text, never as markup", () => {
    render(<Avatar me={{ ...alice, email: "<b>x</b>@evil.example" }} />);
    expect(screen.getByRole("tooltip")).toHaveTextContent("<b>x</b>@evil.example");
    expect(screen.getByRole("tooltip").querySelector("b")).toBeNull();
  });
});
