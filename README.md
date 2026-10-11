# tgVisor

Автономный Android-поисковик: Kotlin/Compose для интерфейса, Rust для фильтров и гибридного поиска. ПК периодически готовит описания и снимки индекса; телефон использует E5-small только для запросов.

Текущая версия: **v0.1.1**. [Скачать APK для Android arm64](https://github.com/MIvi-test/tg_visor/releases/tag/v0.1.1). Модель и личная база устанавливаются отдельно; в APK их нет. Изменения перечислены в [CHANGELOG](CHANGELOG.md).

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

APK: `android/app/build/outputs/apk/debug/app-debug.apk`. Установка на авторизованный тестовый телефон: `adb install -r <путь-к-APK>`. Текущие релизы распространяются как тестовые debug-сборки; для стабильного выпуска нужен постоянный release-ключ.

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

Авторизацию пользователь выполняет локально. Не передавайте коды, пароль 2FA или session-файлы в чат и не коммитьте их. Скопируйте `indexer/config.example.json` в приватную папку `data/` и задайте явный список чатов. Для регулярной работы скопируйте [.env.example](.env.example) в `.env` и замените примеры своими `TG_API_ID` и `TG_API_HASH`. Локальный `.env` исключён из Git; не включайте его в передаваемые архивы.

Получите `api_id` и `api_hash` на [my.telegram.org](https://my.telegram.org) → **API development tools**. Пошаговое получение и ввод переменных описаны в [руководстве](docs/OPERATIONS.md#получить-telegram-api-id-и-hash). Это не токен BotFather; параметры нужны только сборщику на ПК.

```bash
# Один раз: скопируйте шаблон и заполните .env редактором.
cp -n .env.example .env
chmod 600 .env
# В каждом новом Bash-терминале, из корня проекта:
set -a
source .env
set +a
indexer/.venv/bin/tg-collect data/config.json data/archive
# Один раз: установить локальные Florence/OCR зависимости и веса.
./scripts/setup-florence.sh
indexer/.venv/bin/tg-describe data/archive/selected-catalog.json --device cuda
indexer/.venv/bin/tg-index export data/archive/florence-catalog.json models/e5-small data/archive-v1 --dataset-id personal --version 1
cargo run --locked -p search-cli -- validate data/archive-v1
```

Сборщик не загружает `.env` автоматически: команды выше экспортируют параметры в его окружение. Загружайте только собственный файл — `source` исполняет shell-код.

Основной backend — Florence-2-base-ft для английских подписей и EasyOCR ru/en для надписей. Все вычисления локальны; Ollama не требуется. `meaning` остаётся пустым, E5-small и телефон используют прежний контракт. Вариант Qwen доступен отдельно через `--backend ollama` и только loopback HTTP. См. [настройку Florence](docs/FLORENCE.md). Реальный Telegram collector не запускается без выбранных пользователем чатов и авторизации. Сборщик ограничивает RPC до 2/с по умолчанию (`requests_per_second` в конфиге), показывает прогресс и сохраняет `collect.log`; FloodWait отображается явно. См. [настройку лимита и логов](docs/OPERATIONS.md#лимит-запросов-логи-и-прогресс).

Текущая сборка добавляет новые медиа; перепроверка удалений и изменённых подписей ещё не реализована.

## Обновления через папку / Syncthing

```bash
indexer/.venv/bin/tg-index publish data/archive-v1 data/delivery
```

Команда создаёт неизменяемый ZIP и `latest.json`. Синхронизируйте `data/delivery` с телефоном; в приложении выберите «Папка обновлений», затем «Обновить». Пакет сначала копируется и проверяется, после чего устанавливается в приватное хранилище. Не синхронизируйте открытую рабочую базу приложения.

Ручной импорт поддерживается независимо от Syncthing. Старые версии сохраняются на диске; автоматическая уборка и интерфейс отката пока не реализованы. HTTPS, ANN, мобильный реранкер и плагин exteraGram относятся к следующим этапам. Демо не заменяет проверку качества на реальном архиве.

Перед импортом проверьте совместимость `embedding_space`: валидный снимок с другой моделью может стать активным, после чего поиск отключится до импорта совместимого модельного пакета. Подробнее о восстановлении и файловом диалоге — в [диагностике](docs/OPERATIONS.md).

## Сколько времени занимает подготовка базы

Ориентир для ПК с **16 ГБ RAM и RTX 4060 Laptop 8 ГБ VRAM**: Florence на CUDA + EasyOCR ru/en, затем E5-small INT8 на CPU. Время первого скачивания моделей и Telegram-медиа в таблицу не входит.

Измерено: проба Florence/OCR на 10 картинках — 4,2 с; при возобновлении 735 новых картинок — 217,8 с (ещё 438 уже были в кеше). Для расчёта берём **0,3–0,6 с/картинку**, с запасом относительно этих прогонов. Синтетический E5 probe: примерно 4,5 / 11,9 / 46,6 мс на текст из 30 / 100 / 350 слов. Это измерения отдельных этапов, не benchmark полного экспорта.

| Картинок | Florence + OCR, оценка | E5 + SQLite + превью + ZIP, бюджет* | Всего после сбора, оценка |
|---|---|---|---|
| 1 000 | 5–10 мин | 1–3 мин | 6–13 мин |
| 10 000 | 50–100 мин | 10–30 мин | 1–2,2 ч |
| 50 000 | 4,2–8,3 ч | 50–150 мин | 5–10,8 ч |
| 100 000 | 8,3–16,7 ч | 1,7–5 ч | 10–21,7 ч |

\* Для экспорта закладываем 0,06–0,18 с/картинку: один scene-вектор, декодирование превью, запись, хеши и упаковка. Это плановый бюджет, а не замер всего архива. Диск, CPU, длинные тексты, MP4/FFmpeg и множество надписей могут заметно увеличить время. При наличии meaning создаётся второй вектор. Большие объёмы пока не проверялись.

Приближённо: `T = N_new × t_Florence + N_all × t_export + загрузка моделей`. Описания неизменённых картинок берутся из кеша, но текущий exporter каждый раз пересобирает **весь** снимок. Для 1173 картинок ориентир первого прохода после сбора — **7–15 мин**; это расчёт, не суммарный замер завершённого прогона.

Сбор Telegram считайте отдельно: при 2 RPC/с нижняя граница только по limiter — `R / 2` секунд, где R включает историю и все части скачивания, а не только число картинок. Например, 10 000 RPC требуют минимум 83 мин; сеть и FloodWait добавляют время. Для своего архива запустите `tg-describe ... --limit 100 --device cuda` и сравните время/ETA с таблицей: проба пишет отдельный каталог и не заменяет полный.
