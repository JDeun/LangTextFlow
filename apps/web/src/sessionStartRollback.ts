/** Cleanup only a session this tab may have started.
 *
 * A failed device/preflight check is not permission to stop a remote session
 * initiated from a different operator tab. If a server start was attempted,
 * always try its rollback even if stopping local microphone capture fails.
 */
export async function rollbackFailedSessionStart(
  startRequestIssued: boolean,
  operations: {
    stopCapture: () => Promise<void>;
    stopRemote: () => Promise<void>;
  },
): Promise<void> {
  if (!startRequestIssued) return;
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
