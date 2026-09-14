/** Owns batch-workspace state: imported items, per-item analysis progress
 * (polled from the job API), filename edits, and approval. */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { cancelJob, fetchJob, submitAnalysis } from "../../lib/api/analyses";
import type { AnalysisJobView, JobStatus } from "../../lib/api/types";
import type { BatchItem, BatchItemStatus, ImportedFile } from "./types";

const POLL_INTERVAL_MS = 800;

function makeId(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

function statusFromJob(status: JobStatus): BatchItemStatus {
  switch (status) {
    case "queued":
      return "queued";
    case "running":
      return "running";
    case "completed":
      return "needs_review";
    case "failed":
      return "failed";
    case "cancelled":
      return "cancelled";
  }
}

export interface UseBatchWorkspaceResult {
  items: BatchItem[];
  addFiles: (files: ImportedFile[]) => void;
  removeItem: (id: string) => void;
  editFilename: (id: string, filename: string) => void;
  approveItem: (id: string) => void;
  unapproveItem: (id: string) => void;
  approveAll: () => void;
  cancelItem: (id: string) => void;
  startAnalysis: (modelId: string) => void;
  isAnalyzing: boolean;
  pendingCount: number;
  approvedCount: number;
}

export function useBatchWorkspace(): UseBatchWorkspaceResult {
  const [items, setItems] = useState<BatchItem[]>([]);
  const itemsRef = useRef(items);
  itemsRef.current = items;
  const pollTimers = useRef(new Map<string, ReturnType<typeof setTimeout>>());
  // Ids the user has cancelled or removed. Submission and poll responses for
  // these ids are already in flight when that happens, so this is checked
  // when they land, to stop a late response from reviving state the user
  // already dismissed (and to cancel a backend job that wasn't known yet).
  const stoppedIds = useRef(new Set<string>());

  useEffect(() => {
    const timers = pollTimers.current;
    return () => {
      for (const timer of timers.values()) {
        clearTimeout(timer);
      }
      timers.clear();
    };
  }, []);

  const updateItem = useCallback((id: string, patch: Partial<BatchItem>) => {
    setItems((prev) => prev.map((item) => (item.id === id ? { ...item, ...patch } : item)));
  }, []);

  const clearPoll = useCallback((id: string) => {
    const timer = pollTimers.current.get(id);
    if (timer) {
      clearTimeout(timer);
      pollTimers.current.delete(id);
    }
  }, []);

  const pollJob = useCallback(
    (id: string, jobId: string) => {
      const timer = setTimeout(() => {
        void (async () => {
          let job: AnalysisJobView;
          try {
            job = await fetchJob(jobId);
          } catch (err) {
            pollTimers.current.delete(id);
            if (!stoppedIds.current.has(id)) {
              updateItem(id, { status: "failed", error: errorMessage(err) });
            }
            return;
          }
          pollTimers.current.delete(id);
          if (stoppedIds.current.has(id)) {
            return;
          }
          const status = statusFromJob(job.status);
          updateItem(id, {
            status,
            proposal: job.proposal,
            metrics: job.metrics,
            error: job.error,
          });
          if (status === "queued" || status === "running") {
            pollJob(id, jobId);
          }
        })();
      }, POLL_INTERVAL_MS);
      pollTimers.current.set(id, timer);
    },
    [updateItem],
  );

  const addFiles = useCallback((files: ImportedFile[]) => {
    setItems((prev) => [
      ...prev,
      ...files.map(
        ({ file, sourcePath }): BatchItem => ({
          id: makeId(),
          file,
          sourcePath,
          status: "pending",
          jobId: null,
          proposal: null,
          editedFilename: null,
          metrics: null,
          error: null,
        }),
      ),
    ]);
  }, []);

  const removeItem = useCallback(
    (id: string) => {
      stoppedIds.current.add(id);
      const item = itemsRef.current.find((candidate) => candidate.id === id);
      if (item?.jobId && (item.status === "queued" || item.status === "running")) {
        void cancelJob(item.jobId).catch(() => {});
      }
      clearPoll(id);
      setItems((prev) => prev.filter((candidate) => candidate.id !== id));
    },
    [clearPoll],
  );

  const editFilename = useCallback(
    (id: string, filename: string) => updateItem(id, { editedFilename: filename }),
    [updateItem],
  );

  const approveItem = useCallback((id: string) => updateItem(id, { status: "approved" }), [updateItem]);
  const unapproveItem = useCallback(
    (id: string) => updateItem(id, { status: "needs_review" }),
    [updateItem],
  );

  const approveAll = useCallback(() => {
    setItems((prev) =>
      prev.map((item) => (item.status === "needs_review" ? { ...item, status: "approved" } : item)),
    );
  }, []);

  const cancelItem = useCallback(
    (id: string) => {
      stoppedIds.current.add(id);
      const item = itemsRef.current.find((candidate) => candidate.id === id);
      if (item?.jobId) {
        void cancelJob(item.jobId).catch(() => {});
      }
      clearPoll(id);
      updateItem(id, { status: "cancelled" });
    },
    [clearPoll, updateItem],
  );

  const startAnalysis = useCallback(
    (modelId: string) => {
      const toSubmit = itemsRef.current.filter((item) => item.status === "pending");
      if (toSubmit.length === 0) {
        return;
      }
      setItems((prev) =>
        prev.map((item) => (item.status === "pending" ? { ...item, status: "queued" } : item)),
      );
      for (const item of toSubmit) {
        void submitAnalysis(item.file, modelId)
          .then((job) => {
            if (stoppedIds.current.has(item.id)) {
              // Cancelled/removed while the upload was in flight: this is
              // the first point a real job id exists, so it's the first
              // point cancellation can actually reach the backend.
              void cancelJob(job.id).catch(() => {});
              return;
            }
            updateItem(item.id, { status: statusFromJob(job.status), jobId: job.id });
            pollJob(item.id, job.id);
          })
          .catch((err) => {
            if (stoppedIds.current.has(item.id)) {
              return;
            }
            updateItem(item.id, { status: "failed", error: errorMessage(err) });
          });
      }
    },
    [pollJob, updateItem],
  );

  const isAnalyzing = useMemo(
    () => items.some((item) => item.status === "queued" || item.status === "running"),
    [items],
  );
  const pendingCount = useMemo(() => items.filter((item) => item.status === "pending").length, [items]);
  const approvedCount = useMemo(
    () => items.filter((item) => item.status === "approved").length,
    [items],
  );

  return {
    items,
    addFiles,
    removeItem,
    editFilename,
    approveItem,
    unapproveItem,
    approveAll,
    cancelItem,
    startAnalysis,
    isAnalyzing,
    pendingCount,
    approvedCount,
  };
}
