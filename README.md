# Поиск картинок Telegram

Автономный Android-поисковик: Kotlin/Compose для интерфейса, Rust для фильтров и гибридного поиска. ПК периодически готовит описания и снимки индекса; телефон использует E5-small только для запросов.

Для первого использования: [короткий пошаговый гайд](docs/QUICK_START.md).

## Компоненты

- `crates/search-core`: проверка снимка, точный поиск по векторам, SQLite FTS5, RRF и фильтры.
- `crates/search-jni`: открытие/закрытие сессий, поиск, отмена, токенизация.
- `crates/search-cli`: то же ядро на ПК.
- `android`: импорт ZIP, модель ONNX, результаты и переход в Telegram.
- `indexer`: сбор выбранных чатов, локальное описание картинок, экспорт и публикация файлов.
- [Навигация по документации](docs/README.md): [архитектура](docs/ARCHITECTURE.md), [сборка и эксплуатация](docs/OPERATIONS.md), [API](docs/API.md), [контракт](contracts/SNAPSHOT_FORMAT.md), [проверки и ограничения](docs/IMPLEMENTATION_STATUS.md).

## Локальная подготовка

Целевая среда: Linux x86_64, Android arm64 API 26+, Rust 1.90, JDK 17, Gradle 8.11.1, SDK 35, NDK 27.2. Все скачанные инструменты размещаются в `.tools/`, без изменения системных установок. Скрипт подготовки принимает Android SDK licenses для сборки.

```bash
./scripts/bootstrap.sh
source scripts/env.sh
./scripts/download-model.sh
```

## Сборка и проверки

```bash
source scripts/env.sh
cargo fmt --all --check
cargo clippy --workspace --all-targets --locked -- -D warnings
cargo test --workspace --locked
indexer/.venv/bin/python -m unittest discover -s indexer/tests -v
./scripts/build-android.sh
cd android
./gradlew --no-daemon testDebugUnitTest lintDebug
```

APK: `android/app/build/outputs/apk/debug/app-debug.apk`. Установка на авторизованный тестовый телефон: `adb install -r <путь-к-APK>`. Не используйте debug-ключ для публичного выпуска.

## Первый автономный поиск

После скачивания модели подготовьте два отдельных пакета. Каталог демонстрации содержит искусственные описания, а не реальные Telegram-фото.

```bash
mkdir -p data
indexer/.venv/bin/tg-index pack-model models/e5-small data/e5-small.zip
indexer/.venv/bin/tg-index export contracts/demo-catalog.json models/e5-small data/demo-v1 --dataset-id demo --version 1
cargo run --locked -p search-cli -- validate data/demo-v1
indexer/.venv/bin/tg-index pack data/demo-v1 data/demo-v1.zip
indexer/.venv/bin/tg-index query models/e5-small 'спящий человек' data/request.json
cargo run --locked -p search-cli -- search data/demo-v1 data/request.json
```

Передайте `e5-small.zip` и `demo-v1.zip` на телефон. В приложении нажмите «Модель» и «Импорт архива». Затем ищите, например, «спящий человек» или «усталость после работы». Поиск не требует сети; для открытия исходного сообщения нужен доступ Telegram.

## Реальный архив

Авторизацию пользователь выполняет локально. Не передавайте коды, пароль 2FA или session-файлы в чат и не коммитьте их. Скопируйте `indexer/config.example.json` в приватную папку `data/` и задайте явный список чатов. Перед запуском задайте `TG_API_ID` и `TG_API_HASH` в локальной среде, без записи секретов в команды или репозиторий.

```bash
indexer/.venv/bin/tg-collect data/config.json data/archive
# Требуется отдельно запущенный локальный Ollama с выбранной VLM:
indexer/.venv/bin/tg-describe data/archive/selected-catalog.json --model qwen3-vl:4b
indexer/.venv/bin/tg-index export data/archive/described-catalog.json models/e5-small data/archive-v1 --dataset-id personal --version 1
cargo run --locked -p search-cli -- validate data/archive-v1
```

VLM получает изображения только через loopback HTTP; внешние адреса и редиректы запрещены. Модель описания запускается отдельно, чтобы её выгрузить перед генерацией эмбеддингов. Реальный Telegram collector не запускается без выбранных пользователем чатов и авторизации. Текущая сборка добавляет новые медиа; перепроверка удалений и изменённых подписей ещё не реализована.

## Обновления через папку / Syncthing

```bash
indexer/.venv/bin/tg-index publish data/archive-v1 data/delivery
```

Команда создаёт неизменяемый ZIP и `latest.json`. Синхронизируйте `data/delivery` с телефоном; в приложении выберите «Папка обновлений», затем «Обновить». Пакет сначала копируется и проверяется, после чего устанавливается в приватное хранилище. Не синхронизируйте открытую рабочую базу приложения.

Ручной импорт поддерживается независимо от Syncthing. Старые версии сохраняются на диске; автоматическая уборка и интерфейс отката пока не реализованы. HTTPS, ANN, мобильный реранкер и плагин exteraGram относятся к следующим этапам. Демо не заменяет проверку качества на реальном архиве.

Перед импортом проверьте совместимость `embedding_space`: валидный снимок с другой моделью может стать активным, после чего поиск отключится до импорта совместимого модельного пакета. Подробнее о восстановлении и файловом диалоге — в [диагностике](docs/OPERATIONS.md).
