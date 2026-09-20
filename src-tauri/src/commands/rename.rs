//! Tauri commands for the batch rename transaction and its Undo.

use std::fs;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use tauri::AppHandle;

use crate::fs_atomic;
use crate::history::{self, RenameBatchRecord, RenameEntryRecord};

#[derive(Debug, Clone, Deserialize)]
pub struct RenameItemInput {
    /// Client-generated id echoed back on the matching result. `source_path`
    /// alone cannot correlate results back to UI rows, since a duplicate
    /// import can legitimately send the same source path more than once.
    pub request_id: String,
    pub source_path: String,
    pub desired_filename: String,
}

/// Describes what happened to one file during a rename or Undo operation.
/// `source_path`/`destination_path` describe that operation's own direction,
/// so for Undo they carry the previous destination as the source and the
/// restored original path as the destination.
#[derive(Debug, Clone, Serialize)]
#[serde(tag = "outcome", rename_all = "snake_case")]
pub enum RenameItemResult {
    Renamed {
        source_path: String,
        destination_path: String,
    },
    Failed {
        source_path: String,
        message: String,
    },
}

/// Pairs one `rename_batch` result with the request_id its input carried.
#[derive(Debug, Clone, Serialize)]
pub struct RenameItemOutcome {
    pub request_id: String,
    #[serde(flatten)]
    pub result: RenameItemResult,
}

#[derive(Debug, Clone, Serialize)]
pub struct RenameBatchOutcome {
    /// None when nothing in the batch was renamed, so there is nothing to undo.
    pub batch_id: Option<String>,
    pub results: Vec<RenameItemOutcome>,
    /// Set when every rename below actually happened on disk but the Undo
    /// record for them could not be saved - the caller must not silently
    /// drop `results` in that case, only warn that Undo won't cover them.
    pub history_warning: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct UndoBatchOutcome {
    pub batch_id: String,
    pub results: Vec<RenameItemResult>,
    pub history_warning: Option<String>,
}

/// One file's source/destination paths, stripped of the internal
/// `FileIdentity` that undoing a batch needs but the frontend preview has
/// no use for. `still_valid` is `history::validate_entry` run ahead of
/// time, so the preview can flag a file that's been moved or deleted since
/// the batch was recorded before the user commits to undoing it.
#[derive(Debug, Clone, Serialize)]
pub struct BatchSummaryEntry {
    pub source_path: String,
    pub destination_path: String,
    pub still_valid: bool,
}

#[derive(Debug, Clone, Serialize)]
pub struct BatchSummary {
    pub batch_id: String,
    pub applied_at_unix_ms: u128,
    pub item_count: usize,
    pub entries: Vec<BatchSummaryEntry>,
}

fn is_plain_filename(name: &str) -> bool {
    !name.is_empty()
        && name != "."
        && name != ".."
        && !name.contains(['/', '\\'])
        && !name.contains('\0')
}

fn split_stem_and_extension(name: &str) -> (&str, &str) {
    match name.rfind('.') {
        Some(0) | None => (name, ""),
        Some(idx) => (&name[..idx], &name[idx..]),
    }
}

/// Picks a destination path that does not collide with anything already at
/// that name - including a dangling symlink, which `Path::exists` cannot
/// see since it follows symlinks - or with another item's destination
/// earlier in the same batch, by appending " (n)" before the extension.
/// A destination equal to its own source is never a collision, since that
/// rename is a no-op. This is a best-effort pre-check only: the actual
/// rename is what atomically guarantees no overwrite, since a path can
/// still be claimed by something else between this check and that call.
fn resolve_destination(
    dir: &Path,
    desired_filename: &str,
    source_path: &Path,
    claimed: &mut Vec<PathBuf>,
) -> PathBuf {
    let (stem, extension) = split_stem_and_extension(desired_filename);
    let mut candidate = dir.join(desired_filename);
    let mut attempt = 1u32;
    loop {
        let taken_on_disk =
            candidate.as_path() != source_path && candidate.symlink_metadata().is_ok();
        let taken_in_batch = claimed.iter().any(|path| path == &candidate);
        if !taken_on_disk && !taken_in_batch {
            claimed.push(candidate.clone());
            return candidate;
        }
        candidate = dir.join(format!("{stem} ({attempt}){extension}"));
        attempt += 1;
    }
}

/// Validates every source exists and resolves collision-free destinations
/// for the whole batch, or returns every problem found so the caller can
/// abort without renaming anything.
fn preflight(items: &[RenameItemInput]) -> Result<Vec<(String, PathBuf, PathBuf)>, String> {
    if items.is_empty() {
        return Err("no items to rename".to_string());
    }

    let mut problems = Vec::new();
    for item in items {
        if !is_plain_filename(&item.desired_filename) {
            problems.push(format!(
                "{}: invalid destination filename",
                item.source_path
            ));
            continue;
        }
        match fs::metadata(&item.source_path) {
            Ok(meta) if meta.is_file() => {}
            Ok(_) => problems.push(format!("{}: not a file", item.source_path)),
            Err(_) => problems.push(format!("{}: no longer exists", item.source_path)),
        }
    }
    if !problems.is_empty() {
        return Err(format!(
            "cannot rename, no changes were made: {}",
            problems.join("; ")
        ));
    }

    let mut claimed = Vec::with_capacity(items.len());
    let mut resolved = Vec::with_capacity(items.len());
    for item in items {
        let source = PathBuf::from(&item.source_path);
        let dir = source
            .parent()
            .ok_or_else(|| format!("{}: has no parent directory", item.source_path))?
            .to_path_buf();
        let destination = resolve_destination(&dir, &item.desired_filename, &source, &mut claimed);
        resolved.push((item.request_id.clone(), source, destination));
    }
    Ok(resolved)
}

/// Renames every approved item. Sources are re-checked immediately before
/// renaming anything (if any is missing, nothing is renamed); once that
/// gate passes, each rename is attempted independently via an atomic
/// no-overwrite primitive (see `fs_atomic`) so one file failing at the OS
/// level (permissions, a mid-flight race, a path that got claimed after
/// preflight) does not block the rest of the batch, and never silently
/// clobbers a file it didn't check. Successfully renamed files are
/// recorded so Undo can reverse them later; if that record fails to save,
/// the real per-file results are still returned rather than discarded -
/// only `history_warning` reports the tracking failure.
#[tauri::command]
pub fn rename_batch(
    app: AppHandle,
    items: Vec<RenameItemInput>,
) -> Result<RenameBatchOutcome, String> {
    let resolved = preflight(&items)?;

    let mut results = Vec::with_capacity(resolved.len());
    let mut entries = Vec::new();
    // Paths renamed successfully but whose identity couldn't be captured,
    // so they have no Undo record even though `results` reports success.
    let mut untracked = Vec::new();
    for (request_id, source, destination) in resolved {
        let result = match fs_atomic::rename_no_replace(&source, &destination) {
            Ok(()) => {
                match history::file_identity(&destination) {
                    Ok(identity) => entries.push(RenameEntryRecord {
                        source_path: source.display().to_string(),
                        destination_path: destination.display().to_string(),
                        identity,
                    }),
                    Err(err) => untracked.push(format!("{}: {err}", destination.display())),
                }
                RenameItemResult::Renamed {
                    source_path: source.display().to_string(),
                    destination_path: destination.display().to_string(),
                }
            }
            Err(err) => RenameItemResult::Failed {
                source_path: source.display().to_string(),
                message: err.to_string(),
            },
        };
        results.push(RenameItemOutcome { request_id, result });
    }

    let mut history_warnings = Vec::new();
    if !untracked.is_empty() {
        history_warnings.push(format!(
            "{} file(s) were renamed but could not be tracked for Undo: {}",
            untracked.len(),
            untracked.join("; ")
        ));
    }

    let batch_id = if entries.is_empty() {
        None
    } else {
        let id = format!("batch-{}", history::now_unix_ms());
        match history::append_batch(
            &app,
            RenameBatchRecord {
                id: id.clone(),
                applied_at_unix_ms: history::now_unix_ms(),
                entries,
            },
        ) {
            Ok(()) => Some(id),
            Err(err) => {
                history_warnings.push(format!(
                    "files were renamed, but the Undo record could not be saved ({err}); Undo will not be available for this batch"
                ));
                None
            }
        }
    };

    let history_warning = if history_warnings.is_empty() {
        None
    } else {
        Some(history_warnings.join("; "))
    };

    Ok(RenameBatchOutcome {
        batch_id,
        results,
        history_warning,
    })
}

/// Reverses one rename batch (any batch still listed by
/// `list_rename_batches`, not necessarily the most recent) identified by
/// `batch_id`. Every entry is validated first (the file at its destination
/// must still be the same file that was renamed there - checked by
/// device/inode identity - and its original name must be free again,
/// checked without following symlinks so a dangling one there still counts
/// as occupied); if any entry fails validation, nothing is reversed. Once
/// validation passes, entries are reversed independently through the same
/// atomic no-overwrite primitive `rename_batch` uses, and any that fail at
/// the OS level stay recorded for a later retry rather than being
/// discarded. Undoing an older batch out of order is safe: a later batch
/// that touched the same file changes its identity, so that entry's
/// validation fails cleanly instead of overwriting the wrong file.
#[tauri::command]
pub fn undo_rename_batch(app: AppHandle, batch_id: String) -> Result<UndoBatchOutcome, String> {
    let Some(batch) = history::list_undoable_batches(&app)?
        .into_iter()
        .find(|batch| batch.id == batch_id)
    else {
        return Err("that rename batch is no longer available to undo".to_string());
    };

    let problems: Vec<String> = batch
        .entries
        .iter()
        .filter_map(|entry| history::validate_entry(entry).err())
        .collect();
    if !problems.is_empty() {
        return Err(format!(
            "cannot undo, no changes were made: {}",
            problems.join("; ")
        ));
    }

    let mut results = Vec::with_capacity(batch.entries.len());
    let mut remaining = Vec::new();
    for entry in batch.entries {
        let source = Path::new(&entry.source_path);
        let destination = Path::new(&entry.destination_path);
        match fs_atomic::rename_no_replace(destination, source) {
            Ok(()) => results.push(RenameItemResult::Renamed {
                source_path: entry.destination_path.clone(),
                destination_path: entry.source_path.clone(),
            }),
            Err(err) => {
                results.push(RenameItemResult::Failed {
                    source_path: entry.destination_path.clone(),
                    message: err.to_string(),
                });
                remaining.push(entry);
            }
        }
    }

    let history_warning = match history::replace_batch_entries(&app, &batch.id, remaining) {
        Ok(()) => None,
        Err(err) => Some(format!(
            "files were restored, but the Undo record could not be updated ({err}); its record may now be stale - check rename_history.json if a later Undo behaves unexpectedly"
        )),
    };

    Ok(UndoBatchOutcome {
        batch_id: batch.id,
        results,
        history_warning,
    })
}

/// Every batch still available to undo, most recent first.
#[tauri::command]
pub fn list_rename_batches(app: AppHandle) -> Result<Vec<BatchSummary>, String> {
    Ok(history::list_undoable_batches(&app)?
        .into_iter()
        .map(|batch| BatchSummary {
            batch_id: batch.id,
            applied_at_unix_ms: batch.applied_at_unix_ms,
            item_count: batch.entries.len(),
            entries: batch
                .entries
                .into_iter()
                .map(|entry| {
                    let still_valid = history::validate_entry(&entry).is_ok();
                    BatchSummaryEntry {
                        source_path: entry.source_path,
                        destination_path: entry.destination_path,
                        still_valid,
                    }
                })
                .collect(),
        })
        .collect())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn unique_test_dir(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "invoice-renamer-rename-tests-{name}-{}",
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).expect("create test dir");
        dir
    }

    fn input(source_path: PathBuf, desired_filename: &str) -> RenameItemInput {
        RenameItemInput {
            request_id: "req-1".to_string(),
            source_path: source_path.display().to_string(),
            desired_filename: desired_filename.to_string(),
        }
    }

    #[test]
    fn plain_filename_rejects_separators_and_empty() {
        assert!(is_plain_filename("2026-01-01_Seller_Item_10-EUR.pdf"));
        assert!(!is_plain_filename(""));
        assert!(!is_plain_filename("."));
        assert!(!is_plain_filename(".."));
        assert!(!is_plain_filename("a/b.pdf"));
        assert!(!is_plain_filename("a\\b.pdf"));
    }

    #[test]
    fn split_stem_and_extension_handles_dotfiles_and_no_extension() {
        assert_eq!(split_stem_and_extension("invoice.pdf"), ("invoice", ".pdf"));
        assert_eq!(split_stem_and_extension("invoice"), ("invoice", ""));
        assert_eq!(split_stem_and_extension(".hidden"), (".hidden", ""));
    }

    #[test]
    fn resolve_destination_is_stable_when_nothing_collides() {
        let dir = unique_test_dir("no-collision");
        let source = dir.join("source.pdf");
        let mut claimed = Vec::new();
        let resolved = resolve_destination(&dir, "invoice.pdf", &source, &mut claimed);
        assert_eq!(resolved, dir.join("invoice.pdf"));
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn resolve_destination_treats_own_source_as_free() {
        let dir = unique_test_dir("own-source-free");
        let source = dir.join("same-name.pdf");
        fs::write(&source, b"pdf").expect("create source file");
        let mut claimed = Vec::new();
        let resolved = resolve_destination(&dir, "same-name.pdf", &source, &mut claimed);
        assert_eq!(resolved, source);
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn resolve_destination_appends_suffix_for_existing_file_on_disk() {
        let dir = unique_test_dir("disk-collide");
        fs::write(dir.join("invoice.pdf"), b"existing").expect("seed existing file");
        let source = dir.join("source.pdf");
        let mut claimed = Vec::new();
        let resolved = resolve_destination(&dir, "invoice.pdf", &source, &mut claimed);
        assert_eq!(resolved, dir.join("invoice (1).pdf"));
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    #[cfg(unix)]
    fn resolve_destination_appends_suffix_for_dangling_symlink_on_disk() {
        let dir = unique_test_dir("dangling-symlink-collide");
        std::os::unix::fs::symlink(dir.join("nowhere"), dir.join("invoice.pdf"))
            .expect("seed dangling symlink");
        let source = dir.join("source.pdf");
        let mut claimed = Vec::new();
        let resolved = resolve_destination(&dir, "invoice.pdf", &source, &mut claimed);
        assert_eq!(resolved, dir.join("invoice (1).pdf"));
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn resolve_destination_appends_suffix_for_batch_internal_collision() {
        let dir = unique_test_dir("batch-collide");
        let source_a = dir.join("a.pdf");
        let source_b = dir.join("b.pdf");
        let mut claimed = Vec::new();
        let first = resolve_destination(&dir, "invoice.pdf", &source_a, &mut claimed);
        let second = resolve_destination(&dir, "invoice.pdf", &source_b, &mut claimed);
        assert_eq!(first, dir.join("invoice.pdf"));
        assert_eq!(second, dir.join("invoice (1).pdf"));
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn preflight_rejects_the_whole_batch_when_one_source_is_missing() {
        let dir = unique_test_dir("preflight-missing-source");
        let present = dir.join("present.pdf");
        fs::write(&present, b"pdf").expect("create present file");
        let items = vec![
            input(present, "renamed.pdf"),
            input(dir.join("missing.pdf"), "also-renamed.pdf"),
        ];

        let err = preflight(&items).expect_err("missing source should abort preflight");
        assert!(err.contains("missing.pdf"));
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn preflight_rejects_invalid_destination_filenames() {
        let dir = unique_test_dir("preflight-invalid-name");
        let source = dir.join("source.pdf");
        fs::write(&source, b"pdf").expect("create source file");
        let items = vec![input(source, "nested/name.pdf")];

        let err = preflight(&items).expect_err("path separators should be rejected");
        assert!(err.contains("invalid destination filename"));
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn preflight_preserves_request_id_and_order() {
        let dir = unique_test_dir("preflight-request-id");
        let source_a = dir.join("a.pdf");
        let source_b = dir.join("b.pdf");
        fs::write(&source_a, b"pdf").unwrap();
        fs::write(&source_b, b"pdf").unwrap();
        let items = vec![
            RenameItemInput {
                request_id: "req-a".to_string(),
                source_path: source_a.display().to_string(),
                desired_filename: "renamed-a.pdf".to_string(),
            },
            RenameItemInput {
                request_id: "req-b".to_string(),
                source_path: source_b.display().to_string(),
                desired_filename: "renamed-b.pdf".to_string(),
            },
        ];

        let resolved = preflight(&items).expect("both sources exist");

        assert_eq!(resolved[0].0, "req-a");
        assert_eq!(resolved[1].0, "req-b");
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn rename_item_outcome_serializes_to_a_flat_object() {
        let renamed = RenameItemOutcome {
            request_id: "req-1".to_string(),
            result: RenameItemResult::Renamed {
                source_path: "/a.pdf".to_string(),
                destination_path: "/b.pdf".to_string(),
            },
        };
        let value = serde_json::to_value(&renamed).unwrap();
        assert_eq!(
            value,
            serde_json::json!({
                "request_id": "req-1",
                "outcome": "renamed",
                "source_path": "/a.pdf",
                "destination_path": "/b.pdf",
            })
        );

        let failed = RenameItemOutcome {
            request_id: "req-2".to_string(),
            result: RenameItemResult::Failed {
                source_path: "/c.pdf".to_string(),
                message: "boom".to_string(),
            },
        };
        let value = serde_json::to_value(&failed).unwrap();
        assert_eq!(
            value,
            serde_json::json!({
                "request_id": "req-2",
                "outcome": "failed",
                "source_path": "/c.pdf",
                "message": "boom",
            })
        );
    }
}
