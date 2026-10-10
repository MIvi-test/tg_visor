//! Thin JNI boundary. Handles are validated IDs; snapshots never cross JNI.
use jni::objects::{JClass, JString};
use jni::sys::{jlong, jstring};
use jni::JNIEnv;
use search_core::{SearchRequest, Snapshot};
use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, AtomicI64, Ordering};
use std::sync::{Arc, Mutex, OnceLock};

struct Session {
    snapshot: Mutex<Snapshot>,
    cancelled: AtomicBool,
}
static SESSIONS: OnceLock<Mutex<HashMap<i64, Arc<Session>>>> = OnceLock::new();
static NEXT: AtomicI64 = AtomicI64::new(1);
static TOKENIZER: OnceLock<Mutex<Option<(String, tokenizers::Tokenizer)>>> = OnceLock::new();

fn sessions() -> &'static Mutex<HashMap<i64, Arc<Session>>> {
    SESSIONS.get_or_init(|| Mutex::new(HashMap::new()))
}

#[no_mangle]
pub extern "system" fn Java_dev_tgfeature_search_NativeBridge_facets(
    mut env: JNIEnv,
    _class: JClass,
    id: jlong,
) -> jstring {
    guarded(&mut env, |_| {
        let session = session(id)?;
        let snapshot = session
            .snapshot
            .lock()
            .map_err(|_| "snapshot unavailable")?;
        snapshot.facets().map_err(|e| e.to_string())
    })
}

fn session(id: i64) -> Result<Arc<Session>, String> {
    sessions()
        .lock()
        .map_err(|_| "session registry unavailable")?
        .get(&id)
        .cloned()
        .ok_or_else(|| "invalid session handle".into())
}

fn output(env: &mut JNIEnv, result: Result<serde_json::Value, String>) -> jstring {
    let value = match result {
        Ok(value) => serde_json::json!({"ok":true,"value":value}),
        Err(message) => serde_json::json!({"ok":false,"error":message}),
    };
    match env.new_string(value.to_string()) {
        Ok(s) => s.into_raw(),
        Err(_) => std::ptr::null_mut(),
    }
}

fn guarded<F>(env: &mut JNIEnv, f: F) -> jstring
where
    F: FnOnce(&mut JNIEnv) -> Result<serde_json::Value, String>,
{
    let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| f(env)))
        .unwrap_or_else(|_| Err("native operation failed".into()));
    output(env, result)
}

#[no_mangle]
pub extern "system" fn Java_dev_tgfeature_search_NativeBridge_version(
    mut env: JNIEnv,
    _class: JClass,
) -> jstring {
    guarded(&mut env, |_| Ok(serde_json::json!(search_core::VERSION)))
}

#[no_mangle]
pub extern "system" fn Java_dev_tgfeature_search_NativeBridge_tokenize(
    mut env: JNIEnv,
    _class: JClass,
    path: JString,
    text: JString,
) -> jstring {
    guarded(&mut env, |env| {
        let path: String = env.get_string(&path).map_err(|e| e.to_string())?.into();
        let text: String = env.get_string(&text).map_err(|e| e.to_string())?.into();
        if text.len() > 8192 {
            return Err("query too long".into());
        }
        let mut cache = TOKENIZER
            .get_or_init(|| Mutex::new(None))
            .lock()
            .map_err(|_| "tokenizer unavailable")?;
        if cache.as_ref().map(|(p, _)| p) != Some(&path) {
            let mut tokenizer =
                tokenizers::Tokenizer::from_file(&path).map_err(|e| e.to_string())?;
            tokenizer
                .with_truncation(Some(tokenizers::TruncationParams {
                    max_length: 512,
                    ..Default::default()
                }))
                .map_err(|e| e.to_string())?;
            *cache = Some((path, tokenizer));
        }
        let tokenizer = &cache.as_ref().ok_or("tokenizer unavailable")?.1;
        let encoding = tokenizer
            .encode(format!("query: {text}"), true)
            .map_err(|e| e.to_string())?;
        Ok(
            serde_json::json!({"input_ids":encoding.get_ids(),"attention_mask":encoding.get_attention_mask(),"token_type_ids":encoding.get_type_ids()}),
        )
    })
}

#[no_mangle]
pub extern "system" fn Java_dev_tgfeature_search_NativeBridge_open(
    mut env: JNIEnv,
    _class: JClass,
    path: JString,
) -> jstring {
    guarded(&mut env, |env| {
        let path: String = env.get_string(&path).map_err(|e| e.to_string())?.into();
        let snapshot = Snapshot::open(path).map_err(|e| e.to_string())?;
        let manifest = snapshot.manifest.clone();
        let id = NEXT.fetch_add(1, Ordering::Relaxed);
        let mut registry = sessions()
            .lock()
            .map_err(|_| "session registry unavailable")?;
        if registry.len() >= 8 {
            return Err("too many open snapshots".into());
        }
        registry.insert(
            id,
            Arc::new(Session {
                snapshot: Mutex::new(snapshot),
                cancelled: AtomicBool::new(false),
            }),
        );
        Ok(serde_json::json!({"handle":id,"manifest":manifest}))
    })
}

#[no_mangle]
pub extern "system" fn Java_dev_tgfeature_search_NativeBridge_search(
    mut env: JNIEnv,
    _class: JClass,
    id: jlong,
    request: JString,
) -> jstring {
    guarded(&mut env, |env| {
        let request: String = env.get_string(&request).map_err(|e| e.to_string())?.into();
        let request: SearchRequest = serde_json::from_str(&request).map_err(|e| e.to_string())?;
        let session = session(id)?;
        let snapshot = session
            .snapshot
            .lock()
            .map_err(|_| "snapshot unavailable")?;
        session.cancelled.store(false, Ordering::Relaxed);
        let hits = snapshot
            .search(&request, &session.cancelled)
            .map_err(|e| e.to_string())?;
        serde_json::to_value(hits).map_err(|e| e.to_string())
    })
}

#[no_mangle]
pub extern "system" fn Java_dev_tgfeature_search_NativeBridge_cancel(
    mut env: JNIEnv,
    _class: JClass,
    id: jlong,
) -> jstring {
    guarded(&mut env, |_| {
        session(id)?.cancelled.store(true, Ordering::Relaxed);
        Ok(serde_json::json!(null))
    })
}

#[no_mangle]
pub extern "system" fn Java_dev_tgfeature_search_NativeBridge_close(
    mut env: JNIEnv,
    _class: JClass,
    id: jlong,
) -> jstring {
    guarded(&mut env, |_| {
        let old = sessions()
            .lock()
            .map_err(|_| "session registry unavailable")?
            .remove(&id);
        if let Some(session) = old {
            session.cancelled.store(true, Ordering::Relaxed);
        }
        Ok(serde_json::json!(null))
    })
}
