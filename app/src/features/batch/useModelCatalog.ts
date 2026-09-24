/** Fetches the model catalog with install status, and exposes download/remove
 * actions. Polls while a download is in flight; otherwise fetches on demand. */

import { useCallback, useEffect, useRef, useState } from "react";
import { deleteModel, downloadModel, fetchModels } from "../../lib/api/models";
import type { ModelStatusEntry } from "../../lib/api/types";

const POLL_INTERVAL_MS = 1000;

function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

export interface UseModelCatalogResult {
  models: ModelStatusEntry[];
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  download: (modelId: string) => Promise<void>;
  remove: (modelId: string) => Promise<void>;
}

export function useModelCatalog(): UseModelCatalogResult {
  const [models, setModels] = useState<ModelStatusEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // Tracks "believed still downloading" across failed polls too, since a
  // fetch error is not evidence the download finished - only a successful
  // response that says otherwise is.
  const downloadingRef = useRef(false);
  const pollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearPoll = useCallback(() => {
    if (pollTimerRef.current) {
      clearTimeout(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }, []);

  const refresh = useCallback(() => {
    // Reschedules through this local function rather than through refresh
    // itself, so the callback never references its own binding.
    const refreshOnce = async (): Promise<void> => {
      clearPoll();
      try {
        const next = await fetchModels();
        setModels(next);
        setError(null);
        downloadingRef.current = next.some((model) => model.status === "downloading");
      } catch (err) {
        setError(errorMessage(err));
      } finally {
        setLoading(false);
      }
      if (downloadingRef.current) {
        pollTimerRef.current = setTimeout(() => void refreshOnce(), POLL_INTERVAL_MS);
      }
    };
    return refreshOnce();
  }, [clearPoll]);

  useEffect(() => {
    void refresh();
    return clearPoll;
  }, [refresh, clearPoll]);

  const download = useCallback(
    async (modelId: string) => {
      try {
        const updated = await downloadModel(modelId);
        setModels((prev) => prev.map((model) => (model.entry.id === modelId ? updated : model)));
        setError(null);
        if (updated.status === "downloading" && !downloadingRef.current) {
          downloadingRef.current = true;
          clearPoll();
          pollTimerRef.current = setTimeout(() => void refresh(), POLL_INTERVAL_MS);
        }
      } catch (err) {
        setError(errorMessage(err));
      }
    },
    [clearPoll, refresh],
  );

  const remove = useCallback(async (modelId: string) => {
    try {
      const updated = await deleteModel(modelId);
      setModels((prev) => prev.map((model) => (model.entry.id === modelId ? updated : model)));
      setError(null);
    } catch (err) {
      setError(errorMessage(err));
    }
  }, []);

  return { models, loading, error, refresh, download, remove };
}
