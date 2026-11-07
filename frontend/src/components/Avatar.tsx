import { useId } from "react";
import type { Me } from "../types";

/**
 * Full-length pictograms, like the signs on restroom doors, so male and female cannot be mixed up
 * at a glance. The neutral one (no gender set) is a plain head and shoulders.
 */
function Figure({ gender }: { gender: Me["gender"] }) {
  return (
    <svg
      viewBox="0 0 24 24"
      width="100%"
      height="100%"
      aria-hidden="true"
      focusable="false"
      data-figure={gender ?? "neutral"}
    >
      {gender === "male" && (
        <>
          <circle cx="12" cy="5" r="2.6" fill="currentColor" />
          {/* shoulders and arms, then two trouser legs */}
          <path d="M7.2 9.4h9.6a1.2 1.2 0 0 1 1.2 1.2v5.2h-1.9v-4.3h-.8v10.6h-2.6v-6.1h-1.4v6.1H9.1V10.7h-.8v4.3H6.4v-4.4a1.2 1.2 0 0 1 .8-1.2z" fill="currentColor" />
        </>
      )}
      {gender === "female" && (
        <>
          <circle cx="12" cy="5" r="2.6" fill="currentColor" />
          {/* a dress that flares out to the hem, with two legs below it */}
          <path d="M9.3 9.4h5.4l3.6 8.1H5.7z" fill="currentColor" />
          <rect x="9.3" y="17.2" width="2" height="4.6" rx="0.6" fill="currentColor" />
          <rect x="12.7" y="17.2" width="2" height="4.6" rx="0.6" fill="currentColor" />
        </>
      )}
      {!gender && (
        <>
          <circle cx="12" cy="8.5" r="3.6" fill="currentColor" />
          <path d="M4.5 21c0-4.4 3.4-7 7.5-7s7.5 2.6 7.5 7z" fill="currentColor" />
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
