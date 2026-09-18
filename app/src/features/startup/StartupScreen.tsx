/** Placeholder shown while the local worker boots, and the failure notice if
 * it never comes up. Mirrors the workspace layout so the window has the app's
 * shape from the first frame instead of appearing empty. */

import "./startup.css";

interface StartupScreenProps {
  error: string | null;
}

export function StartupScreen({ error }: StartupScreenProps) {
  if (error !== null) {
    return (
      <div className="startup">
        <div className="startup__panel" role="alert">
          <h2>Couldn't start the local worker</h2>
          <p className="startup__detail">{error}</p>
          <p className="startup__hint">
            Invoices are analyzed by a worker bundled with the app. Restart Invoice Renamer to try
            again.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="startup" aria-busy="true">
      <p className="startup__status" role="status">
        <span className="startup__spinner" aria-hidden="true" />
        Starting the local worker…
      </p>

      <div className="startup__card">
        <span className="startup__bar startup__bar--heading" />
        <span className="startup__bar startup__bar--wide" />
      </div>

      <div className="startup__card">
        <span className="startup__bar startup__bar--heading" />
        <span className="startup__bar" />
        <span className="startup__bar" />
      </div>

      <div className="startup__card">
        <span className="startup__bar startup__bar--heading" />
        <span className="startup__bar startup__bar--wide" />
        <span className="startup__bar startup__bar--wide" />
      </div>
    </div>
  );
}
