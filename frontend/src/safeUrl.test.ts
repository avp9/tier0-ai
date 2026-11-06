import { safeHttpUrl } from "./safeUrl";

describe("safeHttpUrl", () => {
  it.each([
    ["https://example.atlassian.net/browse/SUP-1", "https://example.atlassian.net/browse/SUP-1"],
    ["http://localhost:8080/x", "http://localhost:8080/x"],
  ])("accepts %s", (input, expected) => {
    expect(safeHttpUrl(input)).toBe(expected);
  });

  it.each([
    "javascript:alert(1)",
    "JaVaScRiPt:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "vbscript:msgbox(1)",
    "file:///etc/passwd",
    "//evil.example/x",
    "/relative/path",
    "not a url",
    "",
  ])("rejects %j", (input) => {
    expect(safeHttpUrl(input)).toBeNull();
  });
});
