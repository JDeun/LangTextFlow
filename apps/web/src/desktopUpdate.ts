interface TauriCoreBridge {
  invoke<T>(command: string, args?: Record<string, unknown>): Promise<T>;
}

declare global {
  interface Window {
    __TAURI__?: {
      core?: TauriCoreBridge;
    };
  }
}

function bridge(): TauriCoreBridge | null {
  if (typeof window === "undefined") return null;
  return window.__TAURI__?.core ?? null;
}

export function desktopUpdateSupported(): boolean {
  return bridge() !== null;
}

export async function checkDesktopUpdate(): Promise<string | null> {
  const core = bridge();
  if (!core) return null;
  return core.invoke<string | null>("check_for_update");
}

export async function installDesktopUpdate(): Promise<void> {
  const core = bridge();
  if (!core) throw new Error("Desktop updater is unavailable in this build.");
  await core.invoke<void>("install_update");
}
