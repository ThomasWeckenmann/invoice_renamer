/** Popup listing every AI model call (prompt + response, in order) made for
 * one invoice - including any repair/retry call beyond the initial extraction,
 * so a stuck or wrong result can be diagnosed from what was actually asked. */

import { useEffect, useRef } from "react";
import type { ModelCall, ModelCallPhase } from "../../../lib/api/types";

interface ModelCallsDialogProps {
  calls: ModelCall[];
  onClose: () => void;
}

const PHASE_LABELS: Record<ModelCallPhase, string> = {
  extraction: "Extraction",
  shortening: "Shortening",
};

// Extraction and shortening are two separate, unrelated model passes (see
// analysis/pipeline.py) - each can have its own repair retry, so retries are
// numbered within their own phase rather than across the whole call list, or
// a shortening call would misleadingly look like another extraction retry.
function callLabel(call: ModelCall, indexInPhase: number): string {
  const phase = PHASE_LABELS[call.phase];
  const ordinal = indexInPhase === 0 ? phase : `${phase} retry ${indexInPhase}`;
  return call.error ? `${ordinal} — failed` : ordinal;
}

// Assigns each call its 0-based index within its own phase (extraction calls
// and shortening calls are numbered separately - see callLabel above).
function withPhaseIndex(calls: ModelCall[]): Array<{ call: ModelCall; indexInPhase: number }> {
  const seenInPhase: Record<ModelCallPhase, number> = { extraction: 0, shortening: 0 };
  return calls.map((call) => {
    const indexInPhase = seenInPhase[call.phase];
    seenInPhase[call.phase] += 1;
    return { call, indexInPhase };
  });
}

export function ModelCallsDialog({ calls, onClose }: ModelCallsDialogProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    dialogRef.current?.showModal();
  }, []);

  return (
    <dialog
      ref={dialogRef}
      className="modal modal--wide"
      role="dialog"
      aria-modal="true"
      aria-labelledby="model-calls-title"
      onClose={onClose}
      onClick={(event) => {
        if (event.target === dialogRef.current) {
          dialogRef.current?.close();
        }
      }}
    >
      <h3 id="model-calls-title">AI calls ({calls.length})</h3>

      {calls.length === 0 ? (
        <p className="modal__lead">No model calls were recorded for this run.</p>
      ) : (
        <div className="model-calls__list">
          {withPhaseIndex(calls).map(({ call, indexInPhase }, index) => (
            <details key={index} className="model-calls__item" open={index === 0}>
              <summary>{callLabel(call, indexInPhase)}</summary>
              <div className="model-calls__section">
                <h4>Prompt</h4>
                <pre className="model-calls__text">{call.prompt}</pre>
              </div>
              <div className="model-calls__section">
                <h4>{call.error ? "Error" : "Response"}</h4>
                <pre
                  className={`model-calls__text${call.error ? " model-calls__text--error" : ""}`}
                >
                  {call.error ?? call.response ?? "—"}
                </pre>
              </div>
            </details>
          ))}
        </div>
      )}

      <div className="modal__actions">
        <button type="button" className="btn" onClick={() => dialogRef.current?.close()}>
          Close
        </button>
      </div>
    </dialog>
  );
}
