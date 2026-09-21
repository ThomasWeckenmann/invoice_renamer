/** Tracks backend-confirmed completion of jobs submitted under an active
 * "unload model after this batch" run, independent of each item's own local
 * lifecycle in useBatchWorkspace, and requests exactly one model unload once
 * every tracked job has settled. */

import { useCallback, useEffect, useRef, useState } from "react";
import { fetchJob } from "../../lib/api/analyses";
import { ApiError } from "../../lib/api/client";
import { unloadModel } from "../../lib/api/memory";
import type { JobStatus } from "../../lib/api/types";

const POLL_INTERVAL_MS = 800;
const BUSY_RETRY_DELAY_MS = 800;

const TERMINAL_STATUSES: ReadonlySet<JobStatus> = new Set(["completed", "failed", "cancelled"]);

function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

export interface UseUnloadAfterBatchResult {
  /** Reserve a tracking slot for one item that's about to be submitted -
   * call this synchronously, before the upload starts, so the batch can't
   * look "finished" (and fire an unload) while a sibling item's upload is
   * still in flight and simply hasn't produced a job id yet. Returns a
   * token to pass to settleSubmission once the submission resolves. */
  beginSubmission: () => string;
  /** Resolve a reserved slot: pass the backend job id on a successful
   * submission (tracking continues under that id, independent of the row's
   * own local lifecycle), or
   * null on a definite rejection, which settles the slot immediately since
   * no backend job was ever created for it. Undefined means the outcome is
   * unknown; leave it outstanding and rely on the backend idle timeout. */
  settleSubmission: (token: string, jobId: string | null | undefined) => void;
  trackingError: string | null;
  unloadError: string | null;
  retryUnload: () => void;
}

export function useUnloadAfterBatch(): UseUnloadAfterBatchResult {
  // Ids from possibly-overlapping runs share one set: whichever run's jobs
  // finish last is the one that drains it to zero and fires the unload.
  // Holds either a real job id or a "pending-N" placeholder reserved by
  // beginSubmission for an upload that hasn't resolved to a job id yet.
  const outstanding = useRef(new Set<string>());
  const nextToken = useRef(0);
  const pollTimers = useRef(new Map<string, ReturnType<typeof setTimeout>>());
  const retryTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mountedRef = useRef(true);
  const [unloadError, setUnloadError] = useState<string | null>(null);
  const [trackingError, setTrackingError] = useState<string | null>(null);

  const clearRetryTimer = useCallback(() => {
    if (retryTimer.current) {
      clearTimeout(retryTimer.current);
      retryTimer.current = null;
    }
  }, []);

  const requestUnload = useCallback(() => {
    clearRetryTimer();
    // Re-checked here, not just at the moment this call was scheduled - a
    // scheduled busy-retry (or a manual retry click) can fire after a new
    // submission started in the meantime. beginSubmission also cancels any
    // pending retry timer, but this guard is what actually matters: it's
    // the one check made at the instant the unload would really happen.
    if (!mountedRef.current || outstanding.current.size > 0) {
      return;
    }
    setUnloadError(null);
    void unloadModel().catch((err: unknown) => {
      if (!mountedRef.current) {
        return;
      }
      if (err instanceof ApiError && err.status === 409) {
        // Something else started using the worker between the last tracked
        // job settling and this call - if it's now tracked too, its own
        // drain will retrigger this; otherwise fall back to a short retry.
        if (outstanding.current.size === 0) {
          retryTimer.current = setTimeout(requestUnload, BUSY_RETRY_DELAY_MS);
        }
        return;
      }
      setUnloadError(errorMessage(err));
    });
  }, [clearRetryTimer]);

  const pollJob = useCallback(
    (jobId: string) => {
      const timer = setTimeout(() => {
        void fetchJob(jobId)
          .then((job) => {
            pollTimers.current.delete(jobId);
            if (!mountedRef.current) {
              return;
            }
            if (!TERMINAL_STATUSES.has(job.status)) {
              pollJob(jobId);
              return;
            }
            outstanding.current.delete(jobId);
            if (outstanding.current.size === 0) {
              requestUnload();
            }
          })
          .catch(() => {
            pollTimers.current.delete(jobId);
            if (!mountedRef.current) {
              return;
            }
            // A polling failure is not proof the backend job settled - keep
            // it tracked and keep trying. The idle timeout is the fallback
            // if this job's status can never be confirmed.
            pollJob(jobId);
          });
      }, POLL_INTERVAL_MS);
      pollTimers.current.set(jobId, timer);
    },
    [requestUnload],
  );

  const beginSubmission = useCallback((): string => {
    // A busy-retry (or the user's manual retry) scheduled before this new
    // submission is now stale intent - requestUnload's own outstanding
    // check would catch it regardless, but cancelling it here avoids an
    // unnecessary unload attempt landing mid-upload.
    clearRetryTimer();
    const token = `pending-${nextToken.current++}`;
    outstanding.current.add(token);
    return token;
  }, [clearRetryTimer]);

  const settleSubmission = useCallback(
    (token: string, jobId: string | null | undefined) => {
      if (!mountedRef.current || !outstanding.current.has(token)) {
        return;
      }
      if (jobId === undefined) {
        setTrackingError(
          "An upload's outcome could not be confirmed. Automatic batch unloading is paused until the workspace reopens; the model will still unload after 5 minutes of backend inactivity.",
        );
        return;
      }
      // Swap the placeholder for the real job id in one synchronous step -
      // the set is never observably empty in between, so a sibling job
      // finishing at this exact moment can't mistake the batch for done.
      outstanding.current.delete(token);
      if (jobId !== null) {
        outstanding.current.add(jobId);
        pollJob(jobId);
        return;
      }
      if (outstanding.current.size === 0) {
        requestUnload();
      }
    },
    [pollJob, requestUnload],
  );

  useEffect(() => {
    mountedRef.current = true;
    const timers = pollTimers.current;
    return () => {
      mountedRef.current = false;
      for (const timer of timers.values()) {
        clearTimeout(timer);
      }
      timers.clear();
      clearRetryTimer();
    };
  }, [clearRetryTimer]);

  return { beginSubmission, settleSubmission, trackingError, unloadError, retryUnload: requestUnload };
}
