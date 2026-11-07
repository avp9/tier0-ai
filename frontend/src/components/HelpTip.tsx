import { useId, useState } from "react";

/**
 * A small "?" next to a term. Hovering, focusing or pressing it shows a one-line explanation.
 * ``term`` is what is being explained, used to name the button for screen readers.
 */
export function HelpTip({ term, children }: { term: string; children: string }) {
  const id = useId();
  const [open, setOpen] = useState(false);
  return (
    <span className={`help${open ? " open" : ""}`}>
      <button
        type="button"
        className="help-button"
        aria-label={`What is ${term}?`}
        aria-describedby={id}
        aria-expanded={open}
        onClick={() => setOpen((was) => !was)}
        onBlur={() => setOpen(false)}
        onKeyDown={(event) => {
          if (event.key === "Escape" && open) {
            event.stopPropagation(); // close the tip, not the dialog around it
            setOpen(false);
          }
        }}
      >
        ?
      </button>
      <span role="tooltip" id={id} className="help-tip">
        {children}
      </span>
    </span>
  );
}
