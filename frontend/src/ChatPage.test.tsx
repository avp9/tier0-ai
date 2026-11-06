import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ChatPage } from "./ChatPage";
import { ME, chatReply, json, pending, stubApi } from "./test/helpers";

const THREAD = "11111111-1111-4111-8111-111111111111";

function renderChat(props: { onSignOut?: () => void; onSessionExpired?: () => void } = {}) {
  const onSignOut = props.onSignOut ?? vi.fn();
  const onSessionExpired = props.onSessionExpired ?? vi.fn();
  render(<ChatPage me={ME} onSignOut={onSignOut} onSessionExpired={onSessionExpired} />);
  return { onSignOut, onSessionExpired, user: userEvent.setup() };
}

async function say(user: ReturnType<typeof userEvent.setup>, text: string) {
  await user.type(screen.getByLabelText("Message"), text);
  await user.click(screen.getByRole("button", { name: "Send" }));
}

const conversation = () => screen.getByRole("log", { name: "Conversation" });

describe("sending messages", () => {
  it("shows both sides, the sources, and clears the box", async () => {
    stubApi({
      "POST /api/chat": () =>
        json(
          chatReply({
            reply: "Set it up from the self-service portal.",
            sources: [
              {
                document_id: "d1",
                title: "VPN Setup Guide",
                category: "IT Support",
                source: "helpdesk-kb",
                score: 0.766,
              },
            ],
          }),
        ),
    });
    const { user } = renderChat();
    await say(user, "How do I set up the VPN?");

    expect(await within(conversation()).findByText("Set it up from the self-service portal.")).toBeVisible();
    expect(within(conversation()).getByText("How do I set up the VPN?")).toBeVisible();
    const sources = screen.getByRole("list", { name: "Sources" });
    expect(within(sources).getByText("VPN Setup Guide")).toBeVisible();
    expect(within(sources).getByText("IT Support")).toBeVisible();
    expect(within(sources).getByText(/KB article d1/)).toHaveTextContent(
      "KB article d1 · Source: helpdesk-kb · Relevance: 0.77",
    );
    expect(screen.getByLabelText("Message")).toHaveValue("");
  });

  it("keeps the conversation going on the same thread", async () => {
    const { sentMessages } = stubApi({ "POST /api/chat": () => json(chatReply()) });
    const { user } = renderChat();
    await say(user, "first");
    await screen.findByText("Here is the answer.");
    await say(user, "second");
    await waitFor(() => expect(sentMessages()).toHaveLength(2));
    expect(sentMessages()[0]?.body).toEqual({ message: "first" });
    expect(sentMessages()[1]?.body).toEqual({ message: "second", thread_id: THREAD });
  });

  it("sends on Enter but only inserts a new line on Shift+Enter", async () => {
    const { sentMessages } = stubApi({ "POST /api/chat": () => json(chatReply()) });
    const { user } = renderChat();
    const box = screen.getByLabelText("Message");
    await user.type(box, "line one{Shift>}{Enter}{/Shift}line two");
    expect(sentMessages()).toHaveLength(0);
    expect(box).toHaveValue("line one\nline two");
    await user.keyboard("{Enter}");
    await waitFor(() => expect(sentMessages()).toHaveLength(1));
    expect(sentMessages()[0]?.body).toEqual({ message: "line one\nline two" });
  });

  it("will not send an empty or whitespace-only message", async () => {
    const { sentMessages } = stubApi({ "POST /api/chat": () => json(chatReply()) });
    const { user } = renderChat();
    const send = screen.getByRole("button", { name: "Send" });
    expect(send).toBeDisabled();
    await user.type(screen.getByLabelText("Message"), "   ");
    expect(send).toBeDisabled();
    await user.keyboard("{Enter}");
    expect(sentMessages()).toHaveLength(0);
  });

  it("limits the message length to what the server accepts", () => {
    stubApi({});
    renderChat();
    expect(screen.getByLabelText("Message")).toHaveAttribute("maxlength", "2000");
  });

  it("shows a progress message and blocks double sends while waiting", async () => {
    let release: (r: Response) => void = () => undefined;
    const { sentMessages } = stubApi({
      "POST /api/chat": () => new Promise<Response>((resolve) => (release = resolve)),
    });
    const { user } = renderChat();
    await say(user, "hello");
    expect(await screen.findByText("Tier0 is thinking…")).toBeVisible();
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
    await user.type(screen.getByLabelText("Message"), "{Enter}");
    expect(sentMessages()).toHaveLength(1);
    release(json(chatReply()));
    await waitFor(() => expect(screen.queryByText("Tier0 is thinking…")).not.toBeInTheDocument());
  });
});

describe("not repeating what is already on screen", () => {
  it("shows an answer's sources once, as the list, not again at the end of the text", async () => {
    stubApi({
      "POST /api/chat": () =>
        json(
          chatReply({
            reply: "Use the portal.\n\nSources: VPN Setup Guide; Remote Access Policy",
            sources: [
              { document_id: "a", title: "VPN Setup Guide", category: "IT Support" },
              { document_id: "b", title: "Remote Access Policy", category: "IT Support" },
            ],
          }),
        ),
    });
    const { user } = renderChat();
    await say(user, "vpn?");
    expect(await screen.findByText("Use the portal.")).toBeVisible();
    expect(screen.queryByText(/Sources: VPN/)).not.toBeInTheDocument();
    expect(within(screen.getByRole("list", { name: "Sources" })).getAllByRole("listitem")).toHaveLength(2);
  });

  it("keeps a Sources line it cannot account for", async () => {
    stubApi({
      "POST /api/chat": () =>
        json(chatReply({ reply: "Answer.\n\nSources: Something else", sources: [{ document_id: "a", title: "VPN", category: "IT" }] })),
    });
    const { user } = renderChat();
    await say(user, "q");
    expect(await screen.findByText(/Sources: Something else/)).toBeVisible();
  });

  it("shows the ticket once, in the card, not also as a long message", async () => {
    stubApi({
      "POST /api/chat": () =>
        json(
          chatReply({
            reply: "Here is the ticket I'm about to create:\n- Requester: alice\n- Title: VPN\nShall I create it?",
            pending_action: pending({
              type: "confirm_ticket",
              prompt: "Here is the ticket I'm about to create:\n- Requester: alice\n- Title: VPN",
              preview: {
                requester_email: "alice@buzztech.example",
                title: "VPN",
                description: "It drops.",
                category: "IT",
                priority: null,
              },
            }),
          }),
        ),
    });
    const { user } = renderChat();
    await say(user, "raise a ticket");
    await screen.findByRole("region", { name: "Ticket to be created" });
    expect(within(conversation()).getByText("Please review the ticket below, then create it or cancel.")).toBeVisible();
    expect(screen.queryByText(/Here is the ticket I'm about to create/)).not.toBeInTheDocument();
  });

  it("shows the agent's clarification when the answer to the preview was unclear", async () => {
    const clarification = "I need a clear answer before I create anything. Reply yes to create the ticket, or no to cancel.";
    stubApi({
      "POST /api/chat": () =>
        json(
          chatReply({
            reply: clarification,
            pending_action: pending({
              type: "confirm_ticket",
              prompt: clarification,
              preview: { requester_email: "a@b.co", title: "T", description: "D", category: "IT", priority: null },
            }),
          }),
        ),
    });
    const { user } = renderChat();
    await say(user, "hmm maybe");
    expect(await within(conversation()).findByText(clarification)).toBeVisible();
    expect(screen.getByRole("region", { name: "Ticket to be created" })).toBeVisible();
  });

  it("does the same when the conversation is restored", async () => {
    sessionStorage.setItem("tier0.thread", THREAD);
    stubApi({
      [`GET /api/chat/${THREAD}`]: () =>
        json({
          thread_id: THREAD,
          messages: [{ role: "user", content: "raise a ticket" }],
          pending_action: pending({
            type: "confirm_ticket",
            prompt: "Here is the ticket I'm about to create:\n- Requester: a@b.co\n- Title: T",
            preview: { requester_email: "a@b.co", title: "T", description: "D", category: "IT", priority: null },
          }),
        }),
    });
    renderChat();
    await screen.findByRole("region", { name: "Ticket to be created" });
    expect(screen.getByText("Please review the ticket below, then create it or cancel.")).toBeVisible();
    expect(screen.queryByText(/Here is the ticket I'm about to create/)).not.toBeInTheDocument();
  });
});

describe("untrusted text", () => {
  it("shows markup in a reply as text and never runs it", async () => {
    const evil = '<img src=x onerror="window.pwned = true"><script>window.pwned = true</script><b>bold</b>';
    stubApi({ "POST /api/chat": () => json(chatReply({ reply: evil })) });
    const { user } = renderChat();
    await say(user, "hi");
    expect(await screen.findByText(evil)).toBeVisible();
    expect(conversation().querySelector("img, script, b")).toBeNull();
    expect((window as unknown as { pwned?: boolean }).pwned).toBeUndefined();
  });

  it("shows markup in the employee's own message and in source titles as text", async () => {
    stubApi({
      "POST /api/chat": () =>
        json(chatReply({ sources: [{ document_id: "d", title: "<i>Title</i>", category: "<u>x</u>" }] })),
    });
    const { user } = renderChat();
    await say(user, "<h1>hi</h1>");
    expect(await screen.findByText("<h1>hi</h1>")).toBeVisible();
    expect(screen.getByText("<i>Title</i>")).toBeVisible();
    expect(conversation().querySelector("h1, i, u")).toBeNull();
  });
});

describe("raising a ticket", () => {
  it("offers a ticket with buttons that answer for the employee", async () => {
    const { sentMessages } = stubApi({
      "POST /api/chat": (_u, init) =>
        JSON.parse(String(init?.body)).message === "yes"
          ? json(chatReply({ reply: "What is the problem?", pending_action: pending({ type: "collect_ticket_details" }) }))
          : json(chatReply({ reply: "I could not find an answer.", pending_action: pending() })),
    });
    const { user } = renderChat();
    await say(user, "Does BuzzTech pay for gyms?");
    const offer = await screen.findByRole("region", { name: "Raise a support ticket?" });
    await user.click(within(offer).getByRole("button", { name: "Yes, raise a ticket" }));
    expect(await screen.findByText("What is the problem?")).toBeVisible();
    expect(sentMessages()[1]?.body).toMatchObject({ message: "yes", thread_id: THREAD });
    expect(screen.queryByRole("region", { name: "Raise a support ticket?" })).not.toBeInTheDocument();
  });

  it("can decline an offer", async () => {
    const { sentMessages } = stubApi({
      "POST /api/chat": (_u, init) =>
        json(
          JSON.parse(String(init?.body)).message === "no"
            ? chatReply({ reply: "No problem." })
            : chatReply({ reply: "No answer found.", pending_action: pending() }),
        ),
    });
    const { user } = renderChat();
    await say(user, "question");
    await user.click(await screen.findByRole("button", { name: "No thanks" }));
    expect(await screen.findByText("No problem.")).toBeVisible();
    expect(sentMessages()[1]?.body).toMatchObject({ message: "no" });
  });

  const preview = {
    requester_email: "alice@buzztech.example",
    title: "VPN drops at home",
    description: "What the employee reported:\n- VPN drops",
    category: "IT" as const,
    priority: null,
  };

  async function reachPreview() {
    const api = stubApi({
      "POST /api/chat": (_u, init) => {
        const message = JSON.parse(String(init?.body)).message;
        if (message === "yes") {
          return json(chatReply({ reply: "Created.", ticket: { ticket_key: "SUP-7", url: "https://example.atlassian.net/browse/SUP-7", status: null } }));
        }
        if (message === "no") return json(chatReply({ reply: "Cancelled." }));
        return json(
          chatReply({
            reply: "Here is the ticket I'm about to create:",
            pending_action: pending({ type: "confirm_ticket", preview }),
          }),
        );
      },
    });
    const view = renderChat();
    await say(view.user, "raise a ticket: VPN drops");
    const card = await screen.findByRole("region", { name: "Ticket to be created" });
    return { ...api, ...view, card };
  }

  it("shows exactly what will be created", async () => {
    const { card } = await reachPreview();
    for (const text of ["alice@buzztech.example", "VPN drops at home", "IT", "Not set"]) {
      expect(within(card).getByText(text)).toBeVisible();
    }
    expect(within(card).getByText(/What the employee reported/)).toBeVisible();
    expect(card.querySelector("dt")).not.toBeNull();
  });

  it("creates the ticket only when the employee presses Create ticket", async () => {
    const { card, user, sentMessages } = await reachPreview();
    expect(sentMessages()).toHaveLength(1); // nothing was confirmed on the employee's behalf
    await user.click(within(card).getByRole("button", { name: "Create ticket" }));
    const created = await screen.findByRole("group", { name: "Created ticket" });
    expect(sentMessages()[1]?.body).toMatchObject({ message: "yes" });
    expect(within(created).getByText("SUP-7")).toBeVisible();
    const link = within(created).getByRole("link", { name: /Open SUP-7/ });
    expect(link).toHaveAttribute("href", "https://example.atlassian.net/browse/SUP-7");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("cancels without creating anything", async () => {
    const { card, user, sentMessages } = await reachPreview();
    await user.click(within(card).getByRole("button", { name: "Cancel" }));
    expect(await screen.findByText("Cancelled.")).toBeVisible();
    expect(sentMessages()[1]?.body).toMatchObject({ message: "no" });
    expect(screen.queryByRole("group", { name: "Created ticket" })).not.toBeInTheDocument();
  });

  it("disables the buttons while the choice is being processed", async () => {
    let release: (r: Response) => void = () => undefined;
    stubApi({
      "POST /api/chat": (_u, init) =>
        JSON.parse(String(init?.body)).message === "yes"
          ? new Promise<Response>((resolve) => (release = resolve))
          : json(chatReply({ pending_action: pending({ type: "confirm_ticket", preview }) })),
    });
    const { user } = renderChat();
    await say(user, "raise a ticket");
    const card = await screen.findByRole("region", { name: "Ticket to be created" });
    await user.click(within(card).getByRole("button", { name: "Create ticket" }));
    expect(within(card).getByRole("button", { name: "Create ticket" })).toBeDisabled();
    expect(within(card).getByRole("button", { name: "Cancel" })).toBeDisabled();
    release(json(chatReply({ reply: "Done" })));
    await screen.findByText("Done");
  });

  it.each([
    "javascript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "not a url",
  ])("shows the ticket key but no link for the unsafe address %j", async (url) => {
    stubApi({
      "POST /api/chat": () => json(chatReply({ ticket: { ticket_key: "SUP-9", url, status: null } })),
    });
    const { user } = renderChat();
    await say(user, "hi");
    const created = await screen.findByRole("group", { name: "Created ticket" });
    expect(within(created).getByText("SUP-9")).toBeVisible();
    expect(within(created).queryByRole("link")).not.toBeInTheDocument();
  });
});

describe("choosing among tickets", () => {
  const options = [
    { ticket_key: "SUP-12", title: "Printer offline", status: "Open" },
    { ticket_key: "SUP-7", title: "Laptop slow", status: "In Progress" },
  ];

  it("lists them as buttons that send the ticket key", async () => {
    const { sentMessages } = stubApi({
      "POST /api/chat": (_u, init) =>
        json(
          JSON.parse(String(init?.body)).message === "SUP-7"
            ? chatReply({ reply: "Ticket SUP-7: Laptop slow" })
            : chatReply({ reply: "You have 2 tickets.", pending_action: pending({ type: "select_ticket", options, prompt: "You have 2 tickets:\n1. SUP-5 ...\n2. SUP-7 ..." }) }),
        ),
    });
    const { user } = renderChat();
    await say(user, "show my tickets");
    const list = await screen.findByRole("region", { name: "Choose a ticket" });
    // The accessible name is the visible text, so voice control can say what it sees.
    const second = within(list).getByRole("button", { name: "SUP-7 Laptop slow In Progress" });
    expect(within(list).getAllByRole("button")).toHaveLength(2);
    expect(within(conversation()).getByText("You have 2 tickets. Choose the one you want to see.")).toBeVisible();
    expect(screen.queryByText("You have 2 tickets.")).not.toBeInTheDocument(); // not the long list
    await user.click(second);
    expect(await screen.findByText("Ticket SUP-7: Laptop slow")).toBeVisible();
    expect(sentMessages()[1]?.body).toMatchObject({ message: "SUP-7" });
  });
});

describe("keyboard focus", () => {
  it("moves to the first button when a decision is needed, and back to the box otherwise", async () => {
    stubApi({
      "POST /api/chat": (_u, init) =>
        json(
          JSON.parse(String(init?.body)).message === "no"
            ? chatReply({ reply: "OK" })
            : chatReply({ reply: "No answer.", pending_action: pending() }),
        ),
    });
    const { user } = renderChat();
    await say(user, "question");
    await waitFor(() => expect(screen.getByRole("button", { name: "Yes, raise a ticket" })).toHaveFocus());
    await user.keyboard("{Tab}{Enter}"); // No thanks, by keyboard alone
    await waitFor(() => expect(screen.getByLabelText("Message")).toHaveFocus());
  });
});

describe("when something goes wrong", () => {
  it("keeps what the employee typed and explains a network failure", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    const { user } = renderChat();
    await say(user, "important question");
    expect(await screen.findByRole("alert")).toHaveTextContent("can't reach the server");
    expect(screen.getByLabelText("Message")).toHaveValue("important question");
    expect(within(conversation()).queryByText("important question")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Send" })).toBeEnabled();
  });

  it("says how long to wait when rate limited", async () => {
    stubApi({
      "POST /api/chat": () => json({ detail: "slow" }, 429, { "Retry-After": "30" }),
    });
    const { user } = renderChat();
    await say(user, "hi");
    expect(await screen.findByRole("alert")).toHaveTextContent("too quickly. Please wait 30 seconds");
  });

  it("shows a generic message for a server error without leaking detail", async () => {
    stubApi({
      "POST /api/chat": () => json({ detail: "Something went wrong. Please try again." }, 500),
    });
    const { user } = renderChat();
    await say(user, "hi");
    expect(await screen.findByRole("alert")).toHaveTextContent("Something went wrong. Please try again.");
  });

  it("clears the error on the next attempt", async () => {
    let attempts = 0;
    stubApi({
      "POST /api/chat": () => (attempts++ === 0 ? json({ detail: "x" }, 500) : json(chatReply())),
    });
    const { user } = renderChat();
    await say(user, "hi");
    await screen.findByRole("alert");
    await user.click(screen.getByRole("button", { name: "Send" }));
    await screen.findByText("Here is the answer.");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("returns to sign-in if the session has expired", async () => {
    stubApi({ "POST /api/chat": () => json({ detail: "Not authenticated" }, 401) });
    const { user, onSessionExpired } = renderChat();
    await say(user, "hi");
    await waitFor(() => expect(onSessionExpired).toHaveBeenCalled());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

describe("coming back to a conversation", () => {
  it("restores the messages and the open question after a reload", async () => {
    sessionStorage.setItem("tier0.thread", THREAD);
    const { calls } = stubApi({
      [`GET /api/chat/${THREAD}`]: () =>
        json({
          thread_id: THREAD,
          messages: [
            { role: "user", content: "Does BuzzTech pay for gyms?" },
            { role: "assistant", content: "Sorry, no reliable answer." },
          ],
          pending_action: pending({ prompt: "Shall I raise a ticket?" }),
        }),
    });
    renderChat();
    expect(await screen.findByText("Sorry, no reliable answer.")).toBeVisible();
    expect(screen.getByText("Does BuzzTech pay for gyms?")).toBeVisible();
    expect(screen.getByText("Shall I raise a ticket?")).toBeVisible();
    expect(screen.getByRole("button", { name: "Yes, raise a ticket" })).toBeVisible();
    expect(calls).toHaveLength(1);
  });

  it("starts fresh if the stored conversation is gone", async () => {
    sessionStorage.setItem("tier0.thread", THREAD);
    stubApi({ [`GET /api/chat/${THREAD}`]: () => json({ detail: "Conversation not found" }, 404) });
    renderChat();
    expect(await screen.findByText(/Ask an IT or HR question/)).toBeVisible();
    expect(sessionStorage.getItem("tier0.thread")).toBeNull();
    expect(screen.getByLabelText("Message")).toBeEnabled();
  });

  it("returns to sign-in if restoring reveals an expired session", async () => {
    sessionStorage.setItem("tier0.thread", THREAD);
    stubApi({ [`GET /api/chat/${THREAD}`]: () => json({ detail: "Not authenticated" }, 401) });
    const { onSessionExpired } = renderChat();
    await waitFor(() => expect(onSessionExpired).toHaveBeenCalled());
  });

  it("starts a new conversation on request, without the old thread", async () => {
    const { sentMessages } = stubApi({ "POST /api/chat": () => json(chatReply()) });
    const { user } = renderChat();
    await say(user, "first topic");
    await screen.findByText("Here is the answer.");
    await user.click(screen.getByRole("button", { name: "New conversation" }));
    expect(screen.queryByText("first topic")).not.toBeInTheDocument();
    expect(sessionStorage.getItem("tier0.thread")).toBeNull();
    await say(user, "second topic");
    await waitFor(() => expect(sentMessages()).toHaveLength(2));
    expect(sentMessages()[1]?.body).toEqual({ message: "second topic" });
  });

  it("remembers the conversation id for this tab only", async () => {
    stubApi({ "POST /api/chat": () => json(chatReply()) });
    const { user } = renderChat();
    await say(user, "hi");
    await screen.findByText("Here is the answer.");
    expect(sessionStorage.getItem("tier0.thread")).toBe(THREAD);
    expect(localStorage.length).toBe(0);
  });
});

describe("page structure", () => {
  it("names the signed-in employee and offers sign out", async () => {
    stubApi({});
    const { user, onSignOut } = renderChat();
    expect(screen.getByRole("banner")).toHaveTextContent("Alice Demo");
    expect(screen.getByRole("main")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Sign out" }));
    expect(onSignOut).toHaveBeenCalled();
  });

  it("tells a new visitor what they can ask", () => {
    stubApi({});
    renderChat();
    expect(screen.getByText(/How do I set up the VPN/)).toBeVisible();
  });

  it("announces the conversation politely to screen readers", () => {
    stubApi({});
    renderChat();
    expect(conversation()).toHaveAttribute("aria-live", "polite");
  });

  it("names ticket buttons by exactly what is shown on them", async () => {
    stubApi({
      "POST /api/chat": () =>
        json(
          chatReply({
            pending_action: pending({
              type: "select_ticket",
              options: [{ ticket_key: "SUP-1", title: "Laptop slow", status: "Open" }],
            }),
          }),
        ),
    });
    const { user } = renderChat();
    await say(user, "show my tickets");
    const button = await screen.findByRole("button", { name: /SUP-1/ });
    expect(button).not.toHaveAttribute("aria-label");
    expect(button).toHaveAccessibleName("SUP-1 Laptop slow Open");
  });
});
