import { type ReactNode } from "react";

/** An icon button for the header. The label names it for screen readers and shows on hover. */
export function HeaderButton({
  label,
  onClick,
  children,
}: {
  label: string;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <span className="avatar-wrap">
      <button type="button" className="header-button" aria-label={label} onClick={onClick}>
        {children}
      </button>
      <span className="avatar-tip" aria-hidden="true">
        {label}
      </span>
    </span>
  );
}
