import { useId } from "react";
import type { Me } from "../types";

const SKIN = "#f0c8a0";
const SKIN_SHADE = "#dba97f";
const INK = "#2b2b2b";

/**
 * Illustrated headshots (head, hair, neck and shoulders), one per gender, so the two cannot be
 * mixed up at a glance. They keep their own colours. The neutral one, for an account with no
 * gender set, is a plain grey head and shoulders.
 */
function Figure({ gender }: { gender: Me["gender"] }) {
  return (
    <svg
      viewBox="0 0 64 64"
      width="100%"
      height="100%"
      aria-hidden="true"
      focusable="false"
      data-figure={gender ?? "neutral"}
    >
      {gender === "male" && (
        <>
          {/* shirt, collar and neck */}
          <path d="M6 64c0-13 11-19 26-19s26 6 26 19z" fill="#1d4ed8" />
          <rect x="27" y="36" width="10" height="12" rx="3" fill={SKIN_SHADE} />
          <path d="M25.5 45.5 32 55l6.5-9.5-3.5-1.5-3 3-3-3z" fill="#ffffff" />
          {/* ears, face, short hair */}
          <circle cx="20.8" cy="29" r="2.4" fill={SKIN_SHADE} />
          <circle cx="43.2" cy="29" r="2.4" fill={SKIN_SHADE} />
          <ellipse cx="32" cy="28" rx="11.2" ry="13.4" fill={SKIN} />
          <path d="M20.2 27c-1.4-9.6 3.6-16.6 11.8-16.6S45.2 17.4 43.8 27c-1.6-4.6-4.2-8-9.4-8.6-5.2.4-11 1.6-14.2 8.6z" fill="#3b2a20" />
          {/* eyes, brows, nose, smile */}
          <circle cx="27.6" cy="29.5" r="1.4" fill={INK} />
          <circle cx="36.4" cy="29.5" r="1.4" fill={INK} />
          <path d="M24.8 25.8q2.8-1.6 5.6 0M33.6 25.8q2.8-1.6 5.6 0" stroke="#3b2a20" strokeWidth="1.3" strokeLinecap="round" fill="none" />
          <path d="M32 30.5v4.2h-1.6" stroke={SKIN_SHADE} strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round" fill="none" />
          <path d="M28.2 36.6q3.8 3 7.6 0" stroke={INK} strokeWidth="1.4" strokeLinecap="round" fill="none" />
        </>
      )}
      {gender === "female" && (
        <>
          {/* long hair behind the shoulders */}
          <path d="M16.5 30C15 14 23 7.5 32 7.5S49 14 47.5 30l1.5 22c.2 3-3.2 3.6-5 1.4L41 50H23l-3 3.4c-1.8 2.2-5.2 1.6-5-1.4z" fill="#6b3f26" />
          {/* top, neck, face */}
          <path d="M8 64c0-13 10-18.5 24-18.5S56 51 56 64z" fill="#be185d" />
          <rect x="27.5" y="36" width="9" height="12" rx="3" fill={SKIN_SHADE} />
          <path d="M26 46c1.6 4 3.8 6 6 6s4.4-2 6-6c-2 1.2-4 1.8-6 1.8s-4-.6-6-1.8z" fill={SKIN_SHADE} />
          <ellipse cx="32" cy="28" rx="10.6" ry="12.8" fill={SKIN} />
          {/* side-parted fringe falling over the forehead */}
          <path d="M21 28c-1.6-11 3.8-17.6 11-17.6S44.6 17 43 28c-.8-5.4-3.6-9-8.4-10.2C29.2 20.2 23.4 22 21 28z" fill="#6b3f26" />
          {/* eyes with lashes, brows, smile */}
          <circle cx="27.8" cy="29.5" r="1.4" fill={INK} />
          <circle cx="36.2" cy="29.5" r="1.4" fill={INK} />
          <path d="M25.6 28.2l-1.2-1M38.4 28.2l1.2-1" stroke={INK} strokeWidth="1" strokeLinecap="round" />
          <path d="M25 26.2q2.8-1.4 5.4 0M33.6 26.2q2.6-1.4 5.4 0" stroke="#4a2c1a" strokeWidth="1.1" strokeLinecap="round" fill="none" />
          <path d="M32 30.8v3.6h-1.4" stroke={SKIN_SHADE} strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round" fill="none" />
          <path d="M28.4 36.4q3.6 3.2 7.2 0" stroke="#b4234f" strokeWidth="1.5" strokeLinecap="round" fill="none" />
        </>
      )}
      {!gender && (
        <>
          <circle cx="32" cy="23" r="10" fill="currentColor" />
          <path d="M10 60c0-12 9.4-19 22-19s22 7 22 19z" fill="currentColor" />
        </>
      )}
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
