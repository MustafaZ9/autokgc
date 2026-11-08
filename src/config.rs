use serde::Deserialize;
use std::fs;
use std::path::Path;

#[derive(Debug, Deserialize, Default)]
pub struct Config {
    #[serde(default = "default_csv")]
    pub default_csv: String,
}

fn default_csv() -> String {
    "lod.csv".to_string()
}

impl Config {
    pub fn load(path: &Path) -> Self {
        if !path.exists() {
            return Config::default();
        }
        match fs::read_to_string(path) {
            Ok(content) => serde_json::from_str(&content).unwrap_or_default(),
            Err(_) => Config::default(),
        }
    }
}
