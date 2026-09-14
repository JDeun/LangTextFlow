const FOCUSABLE_SELECTOR = [
  "button:not([disabled])",
  "a[href]",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

function visibleFocusableElements(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)).filter(
    (element) => !element.hidden && element.getAttribute("aria-hidden") !== "true",
  );
}

export function cycleModalFocus(
  modal: HTMLElement,
  current: Element | null,
  backwards: boolean,
): HTMLElement | null {
  const focusable = visibleFocusableElements(modal);
  if (focusable.length === 0) return null;
  const index = current instanceof HTMLElement ? focusable.indexOf(current) : -1;
  if (backwards) {
    return index <= 0 ? focusable.at(-1) ?? null : focusable[index - 1];
  }
  return index < 0 || index >= focusable.length - 1 ? focusable[0] : focusable[index + 1];
}

export function installModalFocusManagement(): () => void {
  let activeModal: HTMLElement | null = null;
  let returnFocus: HTMLElement | null = null;

  const syncModal = () => {
    const next = document.querySelector<HTMLElement>('[role="dialog"][aria-modal="true"]');
    if (next === activeModal) return;

    if (!next && activeModal) {
      activeModal = null;
      const target = returnFocus;
      returnFocus = null;
      if (target?.isConnected) target.focus();
      return;
    }

    if (next) {
      if (!activeModal) {
        returnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      }
      activeModal = next;
      queueMicrotask(() => {
        if (activeModal !== next) return;
        const target = visibleFocusableElements(next)[0] ?? next;
        if (!next.hasAttribute("tabindex") && target === next) next.tabIndex = -1;
        target.focus();
      });
    }
  };

  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key !== "Tab" || !activeModal) return;
    const next = cycleModalFocus(activeModal, document.activeElement, event.shiftKey);
    if (!next) return;
    event.preventDefault();
    next.focus();
  };

  const observer = new MutationObserver(syncModal);
  observer.observe(document.body, { childList: true, subtree: true });
  document.addEventListener("keydown", onKeyDown, true);
  syncModal();

  return () => {
    observer.disconnect();
    document.removeEventListener("keydown", onKeyDown, true);
  };
}
