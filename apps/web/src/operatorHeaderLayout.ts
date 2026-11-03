const headerOwners = new WeakMap<HTMLElement, symbol>();

/** Keep the independently scrolling controls below the actual operator header. */
export function observeOperatorHeader(header: HTMLElement | null) {
  const shell = header?.parentElement;
  if (!header || !shell || typeof ResizeObserver === "undefined") return;

  const owner = Symbol();
  headerOwners.set(shell, owner);
  let previousHeight = "";
  const measure = () => {
    if (headerOwners.get(shell) !== owner) return;
    const height = `${header.getBoundingClientRect().height}px`;
    if (height === previousHeight) return;
    previousHeight = height;
    shell.style.setProperty("--ltf-operator-header-height", height);
    shell.setAttribute("data-header-measured", "");
  };
  const observer = new ResizeObserver(measure);
  observer.observe(header);
  measure();
  return () => {
    observer.disconnect();
    if (headerOwners.get(shell) !== owner) return;
    headerOwners.delete(shell);
    shell.style.removeProperty("--ltf-operator-header-height");
    shell.removeAttribute("data-header-measured");
  };
}
