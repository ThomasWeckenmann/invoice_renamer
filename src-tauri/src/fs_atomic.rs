//! Atomic, no-overwrite rename of a real filesystem path. `std::fs::rename`
//! silently replaces an existing destination - including a dangling
//! symlink, which `Path::exists` can't even see - so invoice renames and
//! their Undo use this instead: a single atomic no-replace rename syscall
//! (`renameat2`/`RENAME_NOREPLACE` on Linux, `renamex_np`/`RENAME_EXCL` on
//! macOS) that the kernel itself refuses if anything already occupies the
//! destination. A link()-then-unlink() pair was considered and rejected:
//! it has its own race window between the two calls, where a file written
//! to the source in between gets silently deleted by the unlink.

use std::ffi::CString;
use std::io;
use std::path::Path;

#[cfg(any(target_os = "linux", target_os = "macos"))]
fn to_cstring(path: &Path) -> io::Result<CString> {
    use std::os::unix::ffi::OsStrExt;
    CString::new(path.as_os_str().as_bytes())
        .map_err(|_| io::Error::new(io::ErrorKind::InvalidInput, "path contains a NUL byte"))
}

#[cfg(target_os = "linux")]
pub fn rename_no_replace(from: &Path, to: &Path) -> io::Result<()> {
    if from == to {
        return Ok(());
    }
    let from_c = to_cstring(from)?;
    let to_c = to_cstring(to)?;

    let result = unsafe {
        libc::renameat2(
            libc::AT_FDCWD,
            from_c.as_ptr(),
            libc::AT_FDCWD,
            to_c.as_ptr(),
            libc::RENAME_NOREPLACE,
        )
    };
    if result != 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(())
}

#[cfg(target_os = "macos")]
pub fn rename_no_replace(from: &Path, to: &Path) -> io::Result<()> {
    if from == to {
        return Ok(());
    }
    let from_c = to_cstring(from)?;
    let to_c = to_cstring(to)?;

    let result = unsafe { libc::renamex_np(from_c.as_ptr(), to_c.as_ptr(), libc::RENAME_EXCL) };
    if result != 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(())
}

#[cfg(not(any(target_os = "linux", target_os = "macos")))]
pub fn rename_no_replace(from: &Path, to: &Path) -> io::Result<()> {
    // Not a shipping target for this app (macOS/Linux only). A check then
    // plain rename keeps the crate compiling elsewhere without pretending
    // to offer the same atomicity guarantee - Rust's std has no portable
    // atomic no-replace rename, only the Linux/macOS syscalls used above.
    if from == to {
        return Ok(());
    }
    if to.symlink_metadata().is_ok() {
        return Err(io::Error::new(
            io::ErrorKind::AlreadyExists,
            "destination already exists",
        ));
    }
    std::fs::rename(from, to)
}

#[cfg(all(test, any(target_os = "linux", target_os = "macos")))]
mod tests {
    use super::*;
    use std::fs;
    use std::path::PathBuf;

    fn unique_test_dir(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "invoice-renamer-fs-atomic-tests-{name}-{}",
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).expect("create test dir");
        dir
    }

    #[test]
    fn renames_when_destination_is_free() {
        let dir = unique_test_dir("free");
        let from = dir.join("a.pdf");
        let to = dir.join("b.pdf");
        fs::write(&from, b"pdf").unwrap();

        rename_no_replace(&from, &to).expect("rename should succeed");

        assert!(!from.exists());
        assert_eq!(fs::read(&to).unwrap(), b"pdf");
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn refuses_to_replace_an_existing_file() {
        let dir = unique_test_dir("existing-file");
        let from = dir.join("a.pdf");
        let to = dir.join("b.pdf");
        fs::write(&from, b"new").unwrap();
        fs::write(&to, b"old").unwrap();

        let err = rename_no_replace(&from, &to).expect_err("must not overwrite an existing file");

        assert_eq!(err.kind(), io::ErrorKind::AlreadyExists);
        assert!(from.exists(), "source must be untouched on failure");
        assert_eq!(fs::read(&to).unwrap(), b"old");
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn refuses_to_replace_a_dangling_symlink() {
        let dir = unique_test_dir("dangling-symlink");
        let from = dir.join("a.pdf");
        let to = dir.join("b.pdf");
        fs::write(&from, b"new").unwrap();
        std::os::unix::fs::symlink(dir.join("nowhere"), &to).unwrap();

        let err =
            rename_no_replace(&from, &to).expect_err("must not replace a dangling symlink either");

        assert_eq!(err.kind(), io::ErrorKind::AlreadyExists);
        assert!(from.exists(), "source must be untouched on failure");
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn same_path_is_a_no_op() {
        let dir = unique_test_dir("same-path");
        let path = dir.join("a.pdf");
        fs::write(&path, b"pdf").unwrap();

        rename_no_replace(&path, &path).expect("renaming a path onto itself is a no-op success");

        assert_eq!(fs::read(&path).unwrap(), b"pdf");
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn preserves_inode_identity_across_the_rename() {
        use std::os::unix::fs::MetadataExt;

        let dir = unique_test_dir("identity");
        let from = dir.join("a.pdf");
        let to = dir.join("b.pdf");
        fs::write(&from, b"pdf").unwrap();
        let ino_before = fs::metadata(&from).unwrap().ino();

        rename_no_replace(&from, &to).unwrap();

        assert_eq!(fs::metadata(&to).unwrap().ino(), ino_before);
        let _ = fs::remove_dir_all(&dir);
    }
}
