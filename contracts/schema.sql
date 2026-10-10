PRAGMA foreign_keys=ON;
CREATE TABLE images (
    id TEXT PRIMARY KEY,
    description TEXT NOT NULL,
    thumbnail TEXT
);
CREATE TABLE messages (
    message_key TEXT PRIMARY KEY,
    image_id TEXT NOT NULL REFERENCES images(id),
    chat_id TEXT NOT NULL,
    chat_name TEXT NOT NULL,
    sender_id TEXT,
    sender_name TEXT,
    username TEXT,
    topic_id TEXT,
    date INTEGER NOT NULL,
    link TEXT,
    caption TEXT NOT NULL DEFAULT ''
);
CREATE INDEX messages_image ON messages(image_id);
CREATE INDEX messages_chat_date ON messages(chat_id,date);
CREATE INDEX messages_sender ON messages(sender_id);
CREATE TABLE representations (
    row_id INTEGER PRIMARY KEY,
    image_id TEXT NOT NULL REFERENCES images(id),
    kind TEXT NOT NULL CHECK(kind IN ('scene','meaning')),
    text TEXT NOT NULL,
    UNIQUE(image_id,kind)
);
CREATE VIRTUAL TABLE search_text USING fts5(image_id UNINDEXED, body, tokenize='unicode61');
PRAGMA user_version=1;
