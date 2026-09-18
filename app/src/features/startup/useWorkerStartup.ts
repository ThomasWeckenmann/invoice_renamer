/** Polls the Tauri shell until the local worker is up, so the app can show a
 * loading state instead of a workspace whose every request would fail. */

import { useEffect, useState } from "react";
import { fetchWorkerStatus } from "../../lib/tauri/worker";

const POLL_INTERVAL_MS = 300;

export interface UseWorkerStartupResult {
  ready: boolean;
  error: string | null;
}

export function useWorkerStartup(): UseWorkerStartupResult {
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const poll = async () => {
      try {
        const status = await fetchWorkerStatus();
        if (cancelled) {
          return;
        }
        if (status.state === "ready") {
          setReady(true);
          return;
        }
        if (status.state === "failed") {
          setError(status.message);
          return;
        }
      } catch (err) {
        // The command is registered before any window exists, so a rejection
        // means the IPC bridge itself is unusable - retrying would just spin.
        if (!cancelled) {
          setError(err instanceof Error ? err.message : String(err));
        }
        return;
      }
      timer = setTimeout(() => void poll(), POLL_INTERVAL_MS);
    };

    void poll();

    return () => {
      cancelled = true;
      if (timer) {
        clearTimeout(timer);
      }
    };
  }, []);

  return { ready, error };
}
