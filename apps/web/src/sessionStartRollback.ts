/** Cleanup only a session this tab may have started.
 *
 * A failed device/preflight check is not permission to stop a remote session
 * initiated from a different operator tab. If a server start was acknowledged,
 * always try its rollback even if stopping local microphone capture fails.
 */
export async function rollbackFailedSessionStart(
  startConfirmed: boolean,
  operations: {
    stopCapture: () => Promise<void>;
    stopRemote: () => Promise<void>;
  },
): Promise<void> {
  if (!startConfirmed) return;
  try {
    await operations.stopCapture();
  } catch {
    // Continue with server-side rollback even if local device cleanup fails.
  }
  try {
    await operations.stopRemote();
  } catch {
    // Keep the original start error; the server may already have stopped.
  }
}
