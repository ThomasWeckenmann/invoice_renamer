"""CLI for the model-selection benchmark: runs the shortlisted local models over a
directory of invoice PDFs and reports field accuracy, correction rate, hallucination
rate, and latency against optional hand-labeled ground truth.

Requires real model downloads (a few GB per model); run on a machine with the disk
and memory to hold them, not in a constrained sandbox.
"""

import argparse
import json
import time
from pathlib import Path

from invoice_renamer.evaluation.benchmark import InvoiceResult, ModelBenchmarkResult, run_benchmark
from invoice_renamer.inference.transformers_extractor import TransformersExtractor
from invoice_renamer.models.catalog_data import SHORTLISTED_CATALOG


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--invoices-dir", required=True, type=Path)
    parser.add_argument("--ground-truth", type=Path, default=None)
    parser.add_argument(
        "--models",
        nargs="+",
        default=[entry.id for entry in SHORTLISTED_CATALOG],
        help="Catalog ids to benchmark (default: the full shortlist)",
    )
    parser.add_argument("--device", default="cpu", help="cpu, mps, or cuda")
    parser.add_argument("--report", type=Path, default=None, help="Write full JSON results here")
    parser.add_argument(
        "--use-xml",
        action="store_true",
        help=(
            "Score the same XML-first routing the app uses instead of this "
            "benchmark's default text-only mode. A complete embedded XML match "
            "then scores as a zero-inference result, not a model result - only "
            "useful when the invoices under test actually carry supported XML."
        ),
    )
    return parser.parse_args()


def _percent_or_unavailable(value: float | None) -> str:
    return "unavailable (no ground truth)" if value is None else f"{value:.0%}"


def _ms_or_unavailable(value: float | None) -> str:
    return "unavailable (every run crashed)" if value is None else f"{value:.0f}"


def _write_report(path: Path, results: list[ModelBenchmarkResult]) -> None:
    # Merges into any existing report by model_id instead of overwriting it,
    # so benchmarking one more model (e.g. via --models) doesn't discard
    # results from an earlier run for models not in this invocation.
    existing: list[dict[str, object]] = json.loads(path.read_text()) if path.exists() else []
    new_by_id = {r.model_id: r.to_dict() for r in results}
    merged = [entry for entry in existing if entry["model_id"] not in new_by_id]
    merged.extend(new_by_id.values())
    path.write_text(json.dumps(merged, indent=2))


def _print_summary(result: ModelBenchmarkResult) -> None:
    print(f"\n=== {result.model_id} ===")
    print(f"invoices: {len(result.invoices)}")
    print(f"failure rate: {result.failure_rate:.0%}")
    print(f"correction rate: {_percent_or_unavailable(result.correction_rate)}")
    print(f"hallucination rate: {_percent_or_unavailable(result.hallucination_rate)}")
    print(f"avg total ms: {_ms_or_unavailable(result.average_total_ms)}")
    print(f"avg inference ms: {_ms_or_unavailable(result.average_inference_ms)}")
    print("field accuracy:")
    for field_name, accuracy in result.field_accuracy.items():
        shown = "n/a" if accuracy is None else f"{accuracy:.0%}"
        print(f"  {field_name}: {shown}")


def main() -> None:
    args = _parse_args()

    ground_truth = None
    if args.ground_truth is not None:
        ground_truth = json.loads(args.ground_truth.read_text())

    catalog_by_id = {entry.id: entry for entry in SHORTLISTED_CATALOG}
    pdf_paths = sorted(args.invoices_dir.glob("*.pdf"))
    if not pdf_paths:
        raise SystemExit(f"no PDFs found in {args.invoices_dir}")

    all_results: list[ModelBenchmarkResult] = []
    for model_id in args.models:
        entry = catalog_by_id[model_id]
        assert entry.repository is not None and entry.revision is not None
        print(f"loading {model_id} ({entry.repository}@{entry.revision[:12]})...", flush=True)
        load_start = time.perf_counter()
        model = TransformersExtractor.load(entry.repository, entry.revision, device=args.device)
        print(f"  loaded in {time.perf_counter() - load_start:.1f}s", flush=True)

        invoice_start_times: dict[str, float] = {}
        current_invoices: list[InvoiceResult] = []

        def _on_start(path: Path) -> None:
            print(f"  [{path.name}] running...", flush=True)
            invoice_start_times[path.name] = time.perf_counter()

        def _on_done(invoice_result: InvoiceResult) -> None:
            elapsed = time.perf_counter() - invoice_start_times[invoice_result.filename]
            status = "crashed" if invoice_result.failed else "ok"
            print(f"  [{invoice_result.filename}] {status} in {elapsed:.1f}s", flush=True)

            # Write after every invoice, not just at the end of a model, so a
            # crash or Ctrl+C mid-model doesn't lose already-finished invoices.
            current_invoices.append(invoice_result)
            if args.report is not None:
                partial = ModelBenchmarkResult(model_id=model_id, invoices=current_invoices)
                _write_report(args.report, [*all_results, partial])

        result = run_benchmark(
            pdf_paths,
            model,
            model_id=model_id,
            model_revision=entry.revision,
            ground_truth=ground_truth,
            on_invoice_start=_on_start,
            on_invoice_done=_on_done,
            use_xml=args.use_xml,
        )
        # Drop the reference before the next iteration loads its model, so the
        # two don't briefly share memory while the next one is loading.
        del model
        _print_summary(result)
        all_results.append(result)
        if args.report is not None:
            _write_report(args.report, all_results)
            print(f"results so far written to {args.report}")


if __name__ == "__main__":
    main()
