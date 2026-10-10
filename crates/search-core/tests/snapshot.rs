use search_core::{
    hash_file, Error, FileEntry, Filters, Manifest, SearchRequest, Snapshot, DIMENSION,
};
use std::collections::BTreeMap;
use std::sync::atomic::AtomicBool;
use std::{fs, path::Path};

fn fixture(root: &Path) {
    let db = rusqlite::Connection::open(root.join("metadata.sqlite")).unwrap();
    db.execute_batch(include_str!("../../../contracts/schema.sql"))
        .unwrap();
    db.execute_batch("INSERT INTO images VALUES('sleep','Человек спит',NULL),('cat','Кот у окна',NULL); INSERT INTO messages(message_key,image_id,chat_id,chat_name,sender_id,date) VALUES('1','sleep','a','Первый','alice',100),('2','cat','b','Второй','bob',200),('3','sleep','b','Второй','bob',300); INSERT INTO representations VALUES(0,'sleep','scene','Человек спит'),(1,'cat','scene','Кот у окна'); INSERT INTO search_text VALUES('sleep','человек спит'),('cat','кот окно');").unwrap();
    drop(db);
    let mut bytes = Vec::new();
    for index in 0..2 {
        for column in 0..DIMENSION {
            bytes.extend_from_slice(&(if index == column { 1.0f32 } else { 0.0 }).to_le_bytes());
        }
    }
    fs::write(root.join("vectors.bin"), bytes).unwrap();
    let mut files = BTreeMap::new();
    for name in ["metadata.sqlite", "vectors.bin"] {
        let path = root.join(name);
        files.insert(
            name.into(),
            FileEntry {
                size: fs::metadata(&path).unwrap().len(),
                sha256: hash_file(&path).unwrap(),
            },
        );
    }
    let manifest = Manifest {
        format_version: 1,
        dataset_id: "test".into(),
        version: 1,
        created_at: "2026-10-10".into(),
        embedding_space: "test".into(),
        model_sha256: "0".repeat(64),
        tokenizer_sha256: "0".repeat(64),
        dimension: DIMENSION,
        dtype: "float32-le".into(),
        normalized: true,
        vector_count: 2,
        image_count: 2,
        files,
    };
    fs::write(
        root.join("manifest.json"),
        serde_json::to_vec(&manifest).unwrap(),
    )
    .unwrap();
}

fn request() -> SearchRequest {
    let mut vector = vec![0.0; DIMENSION];
    vector[0] = 1.0;
    SearchRequest {
        text: String::new(),
        vector: Some(vector),
        embedding_space: Some("test".into()),
        filters: Filters::default(),
        top_k: 10,
    }
}

#[test]
fn exact_search_and_filters_preserve_matching_sources() {
    let dir = tempfile::tempdir().unwrap();
    fixture(dir.path());
    let snapshot = Snapshot::open(dir.path()).unwrap();
    let mut query = request();
    assert_eq!(
        snapshot.search(&query, &AtomicBool::new(false)).unwrap()[0].image_id,
        "sleep"
    );
    query.filters = Filters {
        chat_id: Some("b".into()),
        sender_id: Some("bob".into()),
        from_date: Some(250),
        ..Default::default()
    };
    let hits = snapshot.search(&query, &AtomicBool::new(false)).unwrap();
    assert_eq!(hits.len(), 1);
    assert_eq!(hits[0].sources.len(), 1);
    assert_eq!(hits[0].sources[0].date, 300);
}

#[test]
fn lexical_search_handles_russian_and_fts_punctuation() {
    let dir = tempfile::tempdir().unwrap();
    fixture(dir.path());
    let snapshot = Snapshot::open(dir.path()).unwrap();
    let mut query = request();
    query.vector = None;
    query.embedding_space = None;
    query.text = "\"кот\" * (окно)".into();
    assert_eq!(
        snapshot.search(&query, &AtomicBool::new(false)).unwrap()[0].image_id,
        "cat"
    );
}

#[test]
fn corruption_is_rejected_before_search() {
    let dir = tempfile::tempdir().unwrap();
    fixture(dir.path());
    fs::write(dir.path().join("vectors.bin"), [0u8; 4]).unwrap();
    assert!(matches!(Snapshot::open(dir.path()), Err(Error::Invalid(_))));
}

#[test]
fn incompatible_vectors_and_cancel_are_rejected() {
    let dir = tempfile::tempdir().unwrap();
    fixture(dir.path());
    let snapshot = Snapshot::open(dir.path()).unwrap();
    let mut query = request();
    query.embedding_space = Some("wrong".into());
    assert!(matches!(
        snapshot.search(&query, &AtomicBool::new(false)),
        Err(Error::Invalid(_))
    ));
    assert!(matches!(
        snapshot.search(&request(), &AtomicBool::new(true)),
        Err(Error::Cancelled)
    ));
    query = request();
    query.vector.as_mut().unwrap()[0] = f32::NAN;
    assert!(matches!(
        snapshot.search(&query, &AtomicBool::new(false)),
        Err(Error::Invalid(_))
    ));
}

#[test]
fn path_traversal_is_rejected() {
    let dir = tempfile::tempdir().unwrap();
    fixture(dir.path());
    let path = dir.path().join("manifest.json");
    let mut manifest: Manifest = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
    manifest.files.insert(
        "../outside".into(),
        FileEntry {
            size: 0,
            sha256: "0".repeat(64),
        },
    );
    fs::write(path, serde_json::to_vec(&manifest).unwrap()).unwrap();
    assert!(matches!(Snapshot::open(dir.path()), Err(Error::Invalid(_))));
}

#[test]
fn hybrid_search_has_deterministic_unique_results() {
    let dir = tempfile::tempdir().unwrap();
    fixture(dir.path());
    let snapshot = Snapshot::open(dir.path()).unwrap();
    let mut query = request();
    query.text = "спит".into();
    let hits = snapshot.search(&query, &AtomicBool::new(false)).unwrap();
    assert_eq!(hits.len(), 2);
    assert_eq!(hits[0].image_id, "sleep");
    assert_eq!(hits[0].sources.len(), 2);
}
