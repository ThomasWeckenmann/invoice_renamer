//! Persistent local record of applied rename batches, used to support Undo.

use std::fs;
use std::io;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Manager, Runtime};

const HISTORY_FILE_NAME: &str = "rename_history.json";

/// Filesystem identity used to confirm a path still refers to the same file
/// before Undo reverses a rename, so a file that got replaced at the
/// destination name in the meantime is never silently overwritten by Undo.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct FileIdentity {
    #[cfg(unix)]
    pub device: u64,
    #[cfg(unix)]
    pub inode: u64,
    #[cfg(not(unix))]
    pub len: u64,
    #[cfg(not(unix))]
    pub modified_unix_ms: u128,
}

#[cfg(unix)]
pub fn file_identity(path: &Path) -> io::Result<FileIdentity> {
    use std::os::unix::fs::MetadataExt;
    let meta = fs::metadata(path)?;
    Ok(FileIdentity {
        device: meta.dev(),
        inode: meta.ino(),
    })
}

#[cfg(not(unix))]
pub fn file_identity(path: &Path) -> io::Result<FileIdentity> {
    let meta = fs::metadata(path)?;
    let modified = meta
        .modified()?
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis();
    Ok(FileIdentity {
        len: meta.len(),
        modified_unix_ms: modified,
    })
}

/// One file renamed as part of an applied batch, kept so Undo can reverse it.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct RenameEntryRecord {
    pub source_path: String,
    pub destination_path: String,
    pub identity: FileIdentity,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct RenameBatchRecord {
    pub id: String,
    pub applied_at_unix_ms: u128,
    /// Entries not yet reversed by Undo. An Undo that succeeds for every
    /// entry empties this; one that fails partway leaves the un-reversed
    /// entries here so a retry (or manual recovery) has what it needs.
    pub entries: Vec<RenameEntryRecord>,
}

#[derive(Default, Clone, Debug, Serialize, Deserialize)]
struct HistoryFile {
    batches: Vec<RenameBatchRecord>,
}

fn history_path<R: Runtime>(app: &AppHandle<R>) -> Result<PathBuf, String> {
    let dir = app
        .path()
        .app_data_dir()
        .map_err(|err| format!("could not resolve app data directory: {err}"))?;
    fs::create_dir_all(&dir)
        .map_err(|err| format!("could not create app data directory: {err}"))?;
    Ok(dir.join(HISTORY_FILE_NAME))
}

fn load_from(path: &Path) -> Result<HistoryFile, String> {
    match fs::read_to_string(path) {
        Ok(contents) => serde_json::from_str(&contents)
            .map_err(|err| format!("rename history file is corrupted: {err}")),
        Err(err) if err.kind() == io::ErrorKind::NotFound => Ok(HistoryFile::default()),
        Err(err) => Err(format!("could not read rename history: {err}")),
    }
}

/// Writes via a temp file plus rename so a write that fails partway (a
/// crash, a full disk) can never corrupt or truncate the previous,
/// still-valid history - the old file stays exactly as it was until the
/// new one is complete and the rename swaps it in atomically.
fn save_to(path: &Path, history: &HistoryFile) -> Result<(), String> {
    let json = serde_json::to_string_pretty(history)
        .map_err(|err| format!("could not serialize rename history: {err}"))?;
    let tmp_path = PathBuf::from(format!("{}.tmp", path.display()));
    fs::write(&tmp_path, json).map_err(|err| format!("could not write rename history: {err}"))?;
    fs::rename(&tmp_path, path)
        .map_err(|err| format!("could not finalize rename history write: {err}"))
}

fn load<R: Runtime>(app: &AppHandle<R>) -> Result<HistoryFile, String> {
    load_from(&history_path(app)?)
}

fn save<R: Runtime>(app: &AppHandle<R>, history: &HistoryFile) -> Result<(), String> {
    save_to(&history_path(app)?, history)
}

pub fn append_batch<R: Runtime>(
    app: &AppHandle<R>,
    batch: RenameBatchRecord,
) -> Result<(), String> {
    let mut history = load(app)?;
    history.batches.push(batch);
    save(app, &history)
}

/// The most recent batch that still has entries to reverse, if any.
pub fn last_undoable_batch<R: Runtime>(
    app: &AppHandle<R>,
) -> Result<Option<RenameBatchRecord>, String> {
    let history = load(app)?;
    Ok(history
        .batches
        .into_iter()
        .rev()
        .find(|batch| !batch.entries.is_empty()))
}

/// Replaces a batch's remaining entries, e.g. after Undo reverses some or
/// all of them.
pub fn replace_batch_entries<R: Runtime>(
    app: &AppHandle<R>,
    batch_id: &str,
    entries: Vec<RenameEntryRecord>,
) -> Result<(), String> {
    let mut history = load(app)?;
    if let Some(batch) = history.batches.iter_mut().find(|b| b.id == batch_id) {
        batch.entries = entries;
    }
    save(app, &history)
}

pub fn now_unix_ms() -> u128 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn unique_test_path(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "invoice-renamer-history-tests-{}",
            std::process::id()
        ));
        let _ = fs::create_dir_all(&dir);
        dir.join(format!("{name}.json"))
    }

    #[test]
    fn save_then_load_round_trips() {
        let path = unique_test_path("round-trip");
        let history = HistoryFile {
            batches: vec![RenameBatchRecord {
                id: "batch-1".to_string(),
                applied_at_unix_ms: 42,
                entries: vec![],
            }],
        };

        save_to(&path, &history).expect("save should succeed");
        let loaded = load_from(&path).expect("load should succeed");

        assert_eq!(loaded.batches.len(), 1);
        assert_eq!(loaded.batches[0].id, "batch-1");
        let _ = fs::remove_file(&path);
    }

    #[test]
    fn load_missing_file_returns_empty_history() {
        let path = unique_test_path("missing");
        let _ = fs::remove_file(&path);

        let loaded = load_from(&path).expect("a missing file is an empty history, not an error");

        assert!(loaded.batches.is_empty());
    }

    #[test]
    fn load_corrupt_file_is_a_clear_error() {
        let path = unique_test_path("corrupt");
        fs::write(&path, b"not json").unwrap();

        let err =
            load_from(&path).expect_err("corrupt history should error, not be silently discarded");

        assert!(err.contains("corrupted"));
        let _ = fs::remove_file(&path);
    }

    #[test]
    fn save_does_not_leave_a_temp_file_behind() {
        let path = unique_test_path("no-leftover-tmp");
        save_to(&path, &HistoryFile::default()).expect("save should succeed");

        let tmp_path = PathBuf::from(format!("{}.tmp", path.display()));
        assert!(!tmp_path.exists());
        let _ = fs::remove_file(&path);
    }

    #[test]
    fn save_replaces_previous_content_rather_than_merging() {
        let path = unique_test_path("replace");
        save_to(
            &path,
            &HistoryFile {
                batches: vec![RenameBatchRecord {
                    id: "old".to_string(),
                    applied_at_unix_ms: 1,
                    entries: vec![],
                }],
            },
        )
        .unwrap();

        save_to(&path, &HistoryFile::default()).unwrap();
        let loaded = load_from(&path).unwrap();

        assert!(loaded.batches.is_empty());
        let _ = fs::remove_file(&path);
    }
}
