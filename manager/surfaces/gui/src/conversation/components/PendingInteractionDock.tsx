import { useEffect, useRef, type ReactNode } from "react";

interface Props {
  label: string;
  children: ReactNode;
}

export function PendingInteractionDock({ label, children }: Props) {
  const dockRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    const composer = document.querySelector<HTMLTextAreaElement>(
      "[data-conversation-composer] textarea",
    );
    dockRef.current?.focus({ preventScroll: true });
    return () => {
      window.requestAnimationFrame(() => {
        if (composer?.isConnected) composer.focus({ preventScroll: true });
      });
    };
  }, []);

  return (
    <section
      className="pending-interaction-dock"
      aria-label={label}
      ref={dockRef}
      role="region"
      tabIndex={-1}
    >
      {children}
    </section>
  );
}
