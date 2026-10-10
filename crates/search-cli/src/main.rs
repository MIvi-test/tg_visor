use search_core::{SearchRequest, Snapshot};
use std::sync::atomic::AtomicBool;

fn run() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<String> = std::env::args().collect();
    if args.len() != 3 && args.len() != 4 {
        return Err(
            "usage: search-cli validate SNAPSHOT | search-cli search SNAPSHOT REQUEST.json".into(),
        );
    }
    let snapshot = Snapshot::open(&args[2])?;
    match args[1].as_str() {
        "validate" if args.len() == 3 => {
            println!("{}", serde_json::to_string_pretty(&snapshot.manifest)?)
        }
        "search" if args.len() == 4 => {
            let request: SearchRequest = serde_json::from_reader(std::fs::File::open(&args[3])?)?;
            println!(
                "{}",
                serde_json::to_string_pretty(&snapshot.search(&request, &AtomicBool::new(false))?)?
            );
        }
        _ => return Err("invalid command".into()),
    }
    Ok(())
}

fn main() {
    if let Err(error) = run() {
        eprintln!("{error}");
        std::process::exit(1);
    }
}
