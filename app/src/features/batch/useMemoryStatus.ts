/** Polls GET /memory for a live system/worker/GPU memory snapshot, pausing
 * while the workspace is hidden and backing off after errors. */

import { useCallback, useEffect, useRef, useState } from "react";
import { fetchMemorySnapshot } from "../../lib/api/memory";
import type { MemorySnapshot } from "../../lib/api/types";

const POLL_INTERVAL_MS = 1000;
const MAX_RETRY_DELAY_MS = 10_000;

function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

export interface UseMemoryStatusResult {
  snapshot: MemorySnapshot | null;
  error: string | null;
  stale: boolean;
}

export function useMemoryStatus(): UseMemoryStatusResult {
  const [snapshot, setSnapshot] = useState<MemorySnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [stale, setStale] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Governs whether polling should continue (false while hidden or
  // unmounted) - independent of which specific request is still wanted.
  const activeRef = useRef(false);
  // Bumped by every poll() call (and on unmount), so a response can tell
  // whether it's still the most recently requested one. Without this, a
  // request started before a hide/resume cycle can resolve after the resume
  // has already started a fresh one and clobber it with older data, or
  // schedule a second, overlapping poll chain.
  const generationRef = useRef(0);
  const retryDelayRef = useRef(POLL_INTERVAL_MS);

  const clearTimer = useCallback(() => {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const poll = useCallback(() => {
    // Reschedules through this local function rather than through poll
    // itself, so the callback never references its own binding.
    const pollOnce = () => {
      clearTimer();
      const generation = ++generationRef.current;
      void fetchMemorySnapshot()
        .then((next) => {
          if (generation !== generationRef.current) return;
          setSnapshot(next);
          setError(null);
          setStale(false);
          retryDelayRef.current = POLL_INTERVAL_MS;
        })
        .catch((err: unknown) => {
          if (generation !== generationRef.current) return;
          setError(errorMessage(err));
          setStale(true);
          retryDelayRef.current = Math.min(retryDelayRef.current * 2, MAX_RETRY_DELAY_MS);
        })
        .finally(() => {
          if (generation !== generationRef.current) return;
          if (!activeRef.current) return;
          timerRef.current = setTimeout(pollOnce, retryDelayRef.current);
        });
    };
    pollOnce();
  }, [clearTimer]);

  useEffect(() => {
    const handleVisibility = () => {
      if (document.hidden) {
        activeRef.current = false;
        clearTimer();
      } else {
        activeRef.current = true;
        poll();
      }
    };

    document.addEventListener("visibilitychange", handleVisibility);
    if (!document.hidden) {
      activeRef.current = true;
      poll();
    }

    return () => {
      activeRef.current = false;
      generationRef.current += 1;
      document.removeEventListener("visibilitychange", handleVisibility);
      clearTimer();
    };
  }, [poll, clearTimer]);

  return { snapshot, error, stale };
}
