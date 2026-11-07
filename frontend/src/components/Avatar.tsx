import { useId } from "react";
import type { Me } from "../types";

function Figure({ gender }: { gender: Me["gender"] }) {
  return (
    <svg viewBox="2 3 28 28" width="100%" height="100%" aria-hidden="true" focusable="false">
      {gender === "female" && (
        // Long hair behind the head and shoulders.
        <path d="M8.2 14a7.8 7.8 0 0 1 15.6 0v11.5h-3.2V16h-9.2v9.5H8.2z" fill="currentColor" opacity="0.6" />
      )}
      <circle cx="16" cy="12" r="5.5" fill="currentColor" />
      {gender === "male" && (
        // Short hair on top of the head.
        <path d="M10.2 11.5a5.8 5.8 0 0 1 11.6 0c-2-1.6-3.7-2.2-5.8-2.2s-3.8.6-5.8 2.2z" fill="currentColor" opacity="0.55" />
      )}
      <path d="M5.5 29c0-5.6 4.6-9 10.5-9s10.5 3.4 10.5 9z" fill="currentColor" />
    </svg>
  );
}

/** The signed-in employee as a person icon; hovering or focusing it shows their email. */
export function Avatar({ me }: { me: Me }) {
  const tooltipId = useId();
  return (
    <span className="avatar-wrap">
      {/* A button so the keyboard can reach it and show the email; it does nothing when pressed. */}
      <button
        type="button"
        className={`avatar avatar-${me.gender ?? "neutral"}`}
        aria-label={`Signed in as ${me.display_name}`}
        aria-describedby={tooltipId}
      >
        <Figure gender={me.gender} />
        <span className="visually-hidden">{me.display_name}</span>
      </button>
      <span role="tooltip" id={tooltipId} className="avatar-tip">
        {me.email}
      </span>
    </span>
  );
}
