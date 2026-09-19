//! Tauri build hook, plus a guard against bundling a worker built elsewhere.

use std::path::Path;

/// Where `scripts/build_worker_sidecar.sh` records the triple it staged for.
const WORKER_TARGET_MARKER: &str = "resources/worker.target";
/// The staged worker distribution the bundler is pointed at.
const STAGED_WORKER_DIR: &str = "resources/worker";

fn main() {
    check_staged_worker_target();
    tauri_build::build();
}

/// Fails the build when the staged worker was built for a different target.
///
/// The old `externalBin` sidecar carried its target triple in its filename, so
/// a worker for the wrong architecture was simply not found. A resource
/// directory has no such convention, and this checkout can be mounted on two
/// machines at once, which makes it entirely possible to stage a worker on one
/// and bundle it on the other. Without this check that mistake survives the
/// build and only surfaces as a failed exec at runtime.
fn check_staged_worker_target() {
    println!("cargo:rerun-if-changed={WORKER_TARGET_MARKER}");
    println!("cargo:rerun-if-changed={STAGED_WORKER_DIR}");

    let staged = match std::fs::read_to_string(Path::new(WORKER_TARGET_MARKER)) {
        Ok(contents) => contents,
        // Nothing staged at all. That is the normal state for `cargo check`,
        // `cargo test` and a fresh clone, none of which need a worker; a
        // packaging run fails on its own, because the resource directory the
        // bundler is pointed at does not exist either.
        Err(_) if !Path::new(STAGED_WORKER_DIR).exists() => return,
        // A worker is staged but its provenance is not readable, which is what
        // a half-finished build or a directory copied in by hand looks like.
        // Letting that through would package a worker of unknown architecture.
        Err(err) => panic!(
            "a worker is staged in {STAGED_WORKER_DIR}, but its target marker \
             {WORKER_TARGET_MARKER} could not be read ({err}), so the architecture it was \
             built for is unknown. Re-run scripts/build_worker_sidecar.sh."
        ),
    };

    let target = std::env::var("TARGET").expect("cargo sets TARGET for build scripts");
    let staged = staged.trim();
    if staged != target {
        panic!(
            "the staged worker was built for {staged}, but this build targets {target}. \
             Re-run scripts/build_worker_sidecar.sh."
        );
    }
}
