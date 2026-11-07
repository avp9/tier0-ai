/** Line icons for the header. They inherit the text colour and are hidden from screen readers:
 * the button around each one carries the name. */
function Svg({ children }: { children: React.ReactNode }) {
  return (
    <svg
      viewBox="0 0 24 24"
      width="22"
      height="22"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {children}
    </svg>
  );
}

/** A heart with a heartbeat line through it: the search-quality "health check". */
export function CardiogramIcon() {
  return (
    <Svg>
      <path d="M19.5 12.6 12 20l-7.5-7.4a4.9 4.9 0 0 1 0-7 5 5 0 0 1 7 0l.5.5.5-.5a5 5 0 0 1 7 0 4.9 4.9 0 0 1 0 7z" />
      <path d="M3.5 12h4l1.5-3 3 6 1.8-3h6.2" />
    </Svg>
  );
}

/** A person walking out through an open door. */
export function SignOutIcon() {
  return (
    <Svg>
      {/* the doorway, with its door swung open */}
      <path d="M15 3h5v18h-5" />
      <path d="M15 3l-3 1.6v14.8L15 21" />
      {/* the walker, mid-stride, heading for the door */}
      <circle cx="6.2" cy="5.6" r="1.5" fill="currentColor" stroke="none" />
      <path d="M6.4 8.4 6 13m.2-3.6-2.6 1.6m2.8-1.6 2.4 1.4M6 13l-2 4.4M6 13l2.6 2.2 1 2.6" />
    </Svg>
  );
}
