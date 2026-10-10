//! Portable, read-only search over validated offline snapshots.
use rusqlite::{params, Connection, OpenFlags};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, HashMap, HashSet};
use std::fs::{self, File};
use std::io::{BufReader, Read};
use std::path::{Component, Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};

pub const DIMENSION: usize = 384;
pub const VERSION: &str = env!("CARGO_PKG_VERSION");
const MAX_VECTORS_BYTES: u64 = 512 * 1024 * 1024;

#[derive(Debug, thiserror::Error)]
pub enum Error {
    #[error("invalid snapshot: {0}")]
    Invalid(String),
    #[error("search cancelled")]
    Cancelled,
    #[error(transparent)]
    Io(#[from] std::io::Error),
    #[error(transparent)]
    Json(#[from] serde_json::Error),
    #[error(transparent)]
    Sql(#[from] rusqlite::Error),
}

pub type Result<T> = std::result::Result<T, Error>;

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FileEntry {
    pub size: u64,
    pub sha256: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Manifest {
    pub format_version: u32,
    pub dataset_id: String,
    pub version: u64,
    pub created_at: String,
    pub embedding_space: String,
    pub model_sha256: String,
    pub tokenizer_sha256: String,
    pub dimension: usize,
    pub dtype: String,
    pub normalized: bool,
    pub vector_count: usize,
    pub image_count: usize,
    pub files: BTreeMap<String, FileEntry>,
}

#[derive(Default, Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Filters {
    pub chat_id: Option<String>,
    pub sender_id: Option<String>,
    pub topic_id: Option<String>,
    pub from_date: Option<i64>,
    pub to_date: Option<i64>,
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SearchRequest {
    pub text: String,
    pub vector: Option<Vec<f32>>,
    pub embedding_space: Option<String>,
    #[serde(default)]
    pub filters: Filters,
    #[serde(default = "default_top_k")]
    pub top_k: usize,
}

fn default_top_k() -> usize {
    10
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Source {
    pub chat_id: String,
    pub chat_name: String,
    pub sender_id: Option<String>,
    pub sender_name: Option<String>,
    pub username: Option<String>,
    pub date: i64,
    pub link: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Hit {
    pub image_id: String,
    pub description: String,
    pub thumbnail: Option<String>,
    pub score: f64,
    pub sources: Vec<Source>,
}

pub struct Snapshot {
    pub manifest: Manifest,
    root: PathBuf,
    db: Connection,
    vectors: Vec<f32>,
}

pub fn safe_file(root: &Path, relative: &str) -> Result<PathBuf> {
    let path = Path::new(relative);
    if path.as_os_str().is_empty()
        || path
            .components()
            .any(|c| !matches!(c, Component::Normal(_)))
    {
        return Err(Error::Invalid("unsafe file path".into()));
    }
    let mut current = root.to_path_buf();
    for component in path.components() {
        current.push(component);
        if fs::symlink_metadata(&current)?.file_type().is_symlink() {
            return Err(Error::Invalid("symlinks are not permitted".into()));
        }
    }
    if !current.is_file() {
        return Err(Error::Invalid("expected a regular file".into()));
    }
    Ok(current)
}

pub fn hash_file(path: &Path) -> Result<String> {
    let mut reader = BufReader::new(File::open(path)?);
    let mut hasher = Sha256::new();
    let mut buffer = [0u8; 65536];
    loop {
        let read = reader.read(&mut buffer)?;
        if read == 0 {
            break;
        }
        hasher.update(&buffer[..read]);
    }
    Ok(format!("{:x}", hasher.finalize()))
}

impl Snapshot {
    pub fn open(root: impl AsRef<Path>) -> Result<Self> {
        let root = root.as_ref().canonicalize()?;
        let manifest_path = safe_file(&root, "manifest.json")?;
        if fs::metadata(&manifest_path)?.len() > 16 * 1024 * 1024 {
            return Err(Error::Invalid("manifest too large".into()));
        }
        let manifest: Manifest = serde_json::from_reader(File::open(manifest_path)?)?;
        if manifest.format_version != 1
            || manifest.dimension != DIMENSION
            || manifest.dtype != "float32-le"
            || !manifest.normalized
            || manifest.dataset_id.is_empty()
            || manifest.embedding_space.is_empty()
            || manifest.vector_count == 0
            || manifest.image_count == 0
        {
            return Err(Error::Invalid("unsupported or empty manifest".into()));
        }
        let vector_size = manifest
            .vector_count
            .checked_mul(DIMENSION * 4)
            .ok_or_else(|| Error::Invalid("vector size overflow".into()))?
            as u64;
        if vector_size > MAX_VECTORS_BYTES
            || manifest.files.get("vectors.bin").map(|f| f.size) != Some(vector_size)
            || !manifest.files.contains_key("metadata.sqlite")
        {
            return Err(Error::Invalid(
                "invalid vector size or missing database".into(),
            ));
        }
        let mut total = 0u64;
        for (name, entry) in &manifest.files {
            if name == "metadata.sqlite" && entry.size > 256 * 1024 * 1024 {
                return Err(Error::Invalid("metadata database too large".into()));
            }
            total = total
                .checked_add(entry.size)
                .ok_or_else(|| Error::Invalid("size overflow".into()))?;
            if total > 2 * 1024 * 1024 * 1024 || entry.sha256.len() != 64 {
                return Err(Error::Invalid("snapshot size or hash invalid".into()));
            }
            let path = safe_file(&root, name)?;
            if fs::metadata(&path)?.len() != entry.size || hash_file(&path)? != entry.sha256 {
                return Err(Error::Invalid(format!("file integrity failed: {name}")));
            }
        }
        let db = Connection::open_with_flags(
            root.join("metadata.sqlite"),
            OpenFlags::SQLITE_OPEN_READ_ONLY,
        )?;
        db.execute_batch("PRAGMA query_only=ON; PRAGMA trusted_schema=OFF;")?;
        let integrity: String = db.query_row("PRAGMA quick_check", [], |r| r.get(0))?;
        if integrity != "ok" {
            return Err(Error::Invalid("database integrity failed".into()));
        }
        let rows: usize = db.query_row("SELECT COUNT(*) FROM representations", [], |r| r.get(0))?;
        let images: usize = db.query_row("SELECT COUNT(*) FROM images", [], |r| r.get(0))?;
        let (min, max): (i64, i64) = db.query_row(
            "SELECT MIN(row_id),MAX(row_id) FROM representations",
            [],
            |r| Ok((r.get(0)?, r.get(1)?)),
        )?;
        if rows != manifest.vector_count
            || images != manifest.image_count
            || min != 0
            || max != rows as i64 - 1
        {
            return Err(Error::Invalid("database/vector counts disagree".into()));
        }
        let orphans: usize = db.query_row("SELECT COUNT(*) FROM representations r LEFT JOIN images i ON i.id=r.image_id WHERE i.id IS NULL", [], |r| r.get(0))?;
        let missing_sources: usize = db.query_row("SELECT COUNT(*) FROM images i WHERE NOT EXISTS (SELECT 1 FROM messages m WHERE m.image_id=i.id)", [], |r| r.get(0))?;
        if orphans != 0 || missing_sources != 0 {
            return Err(Error::Invalid("missing image or source".into()));
        }
        let invalid_rows: usize = db.query_row("SELECT COUNT(*) FROM representations WHERE kind NOT IN ('scene','meaning') OR length(text)>32768", [], |r| r.get(0))?;
        let distinct_rows: usize = db.query_row(
            "SELECT COUNT(DISTINCT row_id) FROM representations",
            [],
            |r| r.get(0),
        )?;
        let invalid_images: usize = db.query_row(
            "SELECT COUNT(*) FROM images WHERE length(description)>32768",
            [],
            |r| r.get(0),
        )?;
        if invalid_rows != 0 || invalid_images != 0 || distinct_rows != rows {
            return Err(Error::Invalid(
                "invalid representations or descriptions".into(),
            ));
        }
        let invalid_sources: usize = db.query_row("SELECT COUNT(*) FROM messages WHERE typeof(date)!='integer' OR length(chat_id)>128 OR length(chat_name)>512 OR length(sender_id)>128 OR length(sender_name)>512 OR length(username)>128 OR length(link)>2048 OR length(caption)>32768", [], |r| r.get(0))?;
        if invalid_sources != 0 {
            return Err(Error::Invalid("invalid source metadata".into()));
        }
        let mut stmt = db.prepare("SELECT thumbnail FROM images WHERE thumbnail IS NOT NULL")?;
        for name in stmt.query_map([], |r| r.get::<_, String>(0))? {
            let name = name?;
            if !manifest.files.contains_key(&name) {
                return Err(Error::Invalid("unverified thumbnail".into()));
            }
            safe_file(&root, &name)?;
        }
        drop(stmt);
        // Read a single allocation, rather than duplicating the whole vector file in memory.
        let mut reader = BufReader::new(File::open(root.join("vectors.bin"))?);
        let mut vectors = Vec::with_capacity(rows * DIMENSION);
        let mut buffer = [0u8; DIMENSION * 4];
        for _ in 0..rows {
            reader.read_exact(&mut buffer)?;
            let start = vectors.len();
            for chunk in buffer.chunks_exact(4) {
                let value = f32::from_le_bytes(chunk.try_into().expect("four-byte chunk"));
                if !value.is_finite() {
                    return Err(Error::Invalid("non-finite vector".into()));
                }
                vectors.push(value);
            }
            let norm: f32 = vectors[start..].iter().map(|x| x * x).sum();
            if (norm - 1.0).abs() > 0.02 {
                return Err(Error::Invalid("vector is not normalized".into()));
            }
        }
        // Check that the lexical index can be queried using this SQLite build.
        db.query_row("SELECT COUNT(*) FROM search_text", [], |r| {
            r.get::<_, i64>(0)
        })?;
        Ok(Self {
            manifest,
            root,
            db,
            vectors,
        })
    }

    pub fn root(&self) -> &Path {
        &self.root
    }

    pub fn facets(&self) -> Result<serde_json::Value> {
        let mut chats = self.db.prepare("SELECT chat_id,MAX(chat_name) FROM messages GROUP BY chat_id ORDER BY MAX(chat_name) LIMIT 1000")?;
        let chats = chats
            .query_map([], |r| {
                Ok(serde_json::json!({"id":r.get::<_,String>(0)?,"name":r.get::<_,String>(1)?}))
            })?
            .collect::<std::result::Result<Vec<_>, _>>()?;
        let mut senders = self.db.prepare("SELECT sender_id,COALESCE(MAX(sender_name),sender_id) FROM messages WHERE sender_id IS NOT NULL GROUP BY sender_id ORDER BY MAX(sender_name) LIMIT 1000")?;
        let senders = senders
            .query_map([], |r| {
                Ok(serde_json::json!({"id":r.get::<_,String>(0)?,"name":r.get::<_,String>(1)?}))
            })?
            .collect::<std::result::Result<Vec<_>, _>>()?;
        Ok(serde_json::json!({"chats":chats,"senders":senders}))
    }

    pub fn search(&self, request: &SearchRequest, cancelled: &AtomicBool) -> Result<Vec<Hit>> {
        if !(1..=100).contains(&request.top_k) || request.text.len() > 8192 {
            return Err(Error::Invalid(
                "top_k must be 1..100 and query at most 8192 bytes".into(),
            ));
        }
        let f = &request.filters;
        if matches!((f.from_date, f.to_date), (Some(a), Some(b)) if a > b) {
            return Err(Error::Invalid("invalid date range".into()));
        }
        if let Some(vector) = &request.vector {
            let norm: f32 = vector.iter().map(|x| x * x).sum();
            if vector.len() != DIMENSION
                || vector.iter().any(|x| !x.is_finite())
                || (norm - 1.0).abs() > 0.02
                || request.embedding_space.as_deref() != Some(&self.manifest.embedding_space)
            {
                return Err(Error::Invalid("incompatible query vector".into()));
            }
        }
        let mut allowed_stmt = self.db.prepare(
            "SELECT DISTINCT image_id FROM messages WHERE (?1 IS NULL OR chat_id=?1) AND (?2 IS NULL OR sender_id=?2) AND (?3 IS NULL OR topic_id=?3) AND (?4 IS NULL OR date>=?4) AND (?5 IS NULL OR date<=?5)"
        )?;
        let allowed: HashSet<String> = allowed_stmt
            .query_map(
                params![f.chat_id, f.sender_id, f.topic_id, f.from_date, f.to_date],
                |r| r.get(0),
            )?
            .collect::<std::result::Result<_, _>>()?;
        if cancelled.load(Ordering::Relaxed) {
            return Err(Error::Cancelled);
        }
        let candidate_limit = (request.top_k * 10).clamp(100, 1000);
        let mut branches: Vec<Vec<String>> = Vec::new();
        if let Some(query) = &request.vector {
            let mut scores: BTreeMap<String, HashMap<String, f32>> = BTreeMap::new();
            let mut stmt = self
                .db
                .prepare("SELECT row_id,image_id,kind FROM representations ORDER BY row_id")?;
            let rows = stmt.query_map([], |r| {
                Ok((
                    r.get::<_, usize>(0)?,
                    r.get::<_, String>(1)?,
                    r.get::<_, String>(2)?,
                ))
            })?;
            for row in rows {
                if cancelled.load(Ordering::Relaxed) {
                    return Err(Error::Cancelled);
                }
                let (index, image, kind) = row?;
                if !allowed.contains(&image) {
                    continue;
                }
                let score: f32 = self.vectors[index * DIMENSION..(index + 1) * DIMENSION]
                    .iter()
                    .zip(query)
                    .map(|(a, b)| a * b)
                    .sum();
                scores
                    .entry(kind)
                    .or_default()
                    .entry(image)
                    .and_modify(|s| *s = s.max(score))
                    .or_insert(score);
            }
            for (_, scores) in scores {
                let mut ordered: Vec<_> = scores.into_iter().collect();
                if ordered.len() > candidate_limit {
                    ordered.select_nth_unstable_by(candidate_limit, |a, b| {
                        b.1.total_cmp(&a.1).then_with(|| a.0.cmp(&b.0))
                    });
                    ordered.truncate(candidate_limit);
                }
                ordered.sort_unstable_by(|a, b| b.1.total_cmp(&a.1).then_with(|| a.0.cmp(&b.0)));
                branches.push(
                    ordered
                        .into_iter()
                        .take(candidate_limit)
                        .map(|(id, _)| id)
                        .collect(),
                );
            }
        }
        // Quote tokens: user input is data, not FTS query syntax. Exact tokens use OR for recall.
        let tokens: Vec<String> = request
            .text
            .split(|c: char| !c.is_alphanumeric())
            .filter(|s| !s.is_empty())
            .take(64)
            .map(|s| format!("\"{s}\""))
            .collect();
        if !tokens.is_empty() {
            let mut stmt = self.db.prepare("SELECT image_id FROM search_text WHERE search_text MATCH ?1 AND image_id IN (SELECT image_id FROM messages WHERE (?2 IS NULL OR chat_id=?2) AND (?3 IS NULL OR sender_id=?3) AND (?4 IS NULL OR topic_id=?4) AND (?5 IS NULL OR date>=?5) AND (?6 IS NULL OR date<=?6)) ORDER BY bm25(search_text),image_id LIMIT ?7")?;
            let lexical = stmt
                .query_map(
                    params![
                        tokens.join(" OR "),
                        f.chat_id,
                        f.sender_id,
                        f.topic_id,
                        f.from_date,
                        f.to_date,
                        candidate_limit
                    ],
                    |r| r.get::<_, String>(0),
                )?
                .collect::<std::result::Result<Vec<_>, _>>()?;
            branches.push(lexical);
        }
        let mut fused: HashMap<String, f64> = HashMap::new();
        for branch in branches {
            let mut seen = HashSet::new();
            for (rank, id) in branch.into_iter().enumerate() {
                if seen.insert(id.clone()) {
                    *fused.entry(id).or_default() += 1.0 / (60.0 + rank as f64 + 1.0);
                }
            }
        }
        let mut ordered: Vec<_> = fused.into_iter().collect();
        ordered.sort_unstable_by(|a, b| b.1.total_cmp(&a.1).then_with(|| a.0.cmp(&b.0)));
        let mut hits = Vec::new();
        for (id, score) in ordered.into_iter().take(request.top_k) {
            if cancelled.load(Ordering::Relaxed) {
                return Err(Error::Cancelled);
            }
            let (description, thumbnail) = self.db.query_row(
                "SELECT description,thumbnail FROM images WHERE id=?1",
                [&id],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )?;
            let mut stmt = self.db.prepare("SELECT chat_id,chat_name,sender_id,sender_name,username,date,link FROM messages WHERE image_id=?1 AND (?2 IS NULL OR chat_id=?2) AND (?3 IS NULL OR sender_id=?3) AND (?4 IS NULL OR topic_id=?4) AND (?5 IS NULL OR date>=?5) AND (?6 IS NULL OR date<=?6) ORDER BY date DESC,message_key LIMIT 50")?;
            let sources = stmt
                .query_map(
                    params![
                        id,
                        f.chat_id,
                        f.sender_id,
                        f.topic_id,
                        f.from_date,
                        f.to_date
                    ],
                    |r| {
                        Ok(Source {
                            chat_id: r.get(0)?,
                            chat_name: r.get(1)?,
                            sender_id: r.get(2)?,
                            sender_name: r.get(3)?,
                            username: r.get(4)?,
                            date: r.get(5)?,
                            link: r.get(6)?,
                        })
                    },
                )?
                .collect::<std::result::Result<_, _>>()?;
            hits.push(Hit {
                image_id: id,
                description,
                thumbnail,
                score,
                sources,
            });
        }
        Ok(hits)
    }
}
