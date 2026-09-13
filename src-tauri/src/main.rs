// Binary entrypoint; all app setup lives in the library so it stays testable.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    invoice_renamer_lib::run();
}
