use std::fs::OpenOptions;
use std::io::Write;
use std::path::Path;
use chrono::Local;

/// Log a message to stdout and append to the log file.
pub fn log(message: &str) {
    let timestamp = Local::now().format("%Y-%m-%d %H:%M:%S");
    let entry = format!("{} - {}", timestamp, message);
    println!("{}", entry);

    let log_path = log_file_path();
    if let Ok(mut f) = OpenOptions::new()
        .create(true)
        .append(true)
        .open(&log_path)
    {
        let _ = writeln!(f, "{}", entry);
    }
}

fn log_file_path() -> std::path::PathBuf {
    let exe = std::env::current_exe().unwrap_or_default();
    let dir = exe.parent().unwrap_or(Path::new("."));
    // Prefer the working directory (where known_codes.txt lives)
    let cwd = std::env::current_dir().unwrap_or(dir.to_path_buf());
    cwd.join("redeemed_codes.txt")
}
