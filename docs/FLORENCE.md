# Быстрая разметка через Florence-2

Основной backend — локальная Florence-2-base-ft (230M) для подписи изображения и EasyOCR для русского/английского текста. Ollama для этого пути не нужна. Kotlin/Rust, E5-small и формат снимка остаются прежними.

Расчёты времени для 1k–100k картинок и измерения на ПК с 16 ГБ RAM / 8 ГБ VRAM приведены в [README](../README.md#сколько-времени-занимает-подготовка-базы).

## Установка один раз

Из корня проекта:

```bash
./scripts/setup-florence.sh
```

Скрипт устанавливает optional-зависимости только в `indexer/.venv`, CUDA-библиотеки — в эту же среду; системные драйверы и пакеты не меняются. Требуются исправный NVIDIA-драйвер и несколько гигабайт свободного места. Веса лежат в `models/florence2-base-ft/`, OCR — в `.tools/easyocr/`. Первое скачивание требует интернета; разметка после него работает локально с отключёнными скачиваниями.

Используется native Transformers checkpoint `florence-community/Florence-2-base-ft`, соответствующий Florence Microsoft, revision `0b03b6f15a4a211370fb204aee4e7dd48887ea37`. Все перечисленные файлы проверяются по размеру и SHA-256. `trust_remote_code=False`; файлы Python из репозитория модели не скачиваются и не исполняются. OCR-веса также закреплены по SHA-256; при загрузке EasyOCR принудительно включён tensor-only режим PyTorch. Определения артефактов: [Florence](../indexer/src/tg_indexer/florence_model.json), [OCR](../indexer/src/tg_indexer/ocr_models.json).

Закреплены PyTorch 2.7.1/cu128, torchvision 0.22.1, Transformers 4.57.6 и EasyOCR 1.7.2. `uv.lock` фиксирует транзитивные зависимости. Повторный `bootstrap.sh` устанавливает только базовый runtime и может удалить optional-пакеты; после него повторите `setup-florence.sh`.

## Пробный прогон и полный архив

Команды подходят для Fish и Bash; `source scripts/env.sh` для самой разметки не требуется:

```bash
indexer/.venv/bin/tg-describe data/archive/selected-catalog.json --device cuda --limit 10
```

Проба записывает `florence-probe-catalog.json`, не сокращая полный каталог. Для всех выбранных картинок:

```bash
indexer/.venv/bin/tg-describe data/archive/selected-catalog.json --device cuda
```

Результат — **`data/archive/florence-catalog.json`**, лог — `data/archive/florence.log`. Старый `described-catalog.json` от Qwen не перезаписывается. Повторный запуск использует совпадающий кеш; можно безопасно остановить через Ctrl+C и продолжить. Два процесса в один выходной файл запускать нельзя.

Интерфейс показывает время каждой картинки и примерный остаток. ETA исключает загрузку модели и рассчитывается по обработанным новым картинкам; ранние оценки нестабильны. `--device auto` выбирает CUDA при доступности, иначе CPU; явный `--device cuda` не допускает тихого перехода на CPU.

## Что записывается в каталог

| Поле | Источник |
|---|---|
| `scene` | Английская подпись Florence `<DETAILED_CAPTION>`, максимум 128 новых токенов по умолчанию |
| `ocr` | EasyOCR ru/en, только результаты с confidence ≥ 0,3, до 16000 символов |
| `meaning` | Пустая строка; интерпретация шутки не генерируется |
| `sources` | Исходные Telegram-источники, подписи, даты и отправители |
| `analysis_profile` | Revision, задача, бюджет, OCR, хеши OCR-весов и настройки подготовки |

Изображение приводится к RGB с учётом EXIF и уменьшается до 1600 по длинной стороне. Для уже скачанных анимированных Telegram-фото в MP4 извлекается первый кадр через установленный FFmpeg; оригинал сохраняется. FFmpeg принудительно использует MOV/MP4 demuxer, без внешних ссылок и сетевых протоколов, с таймаутом 30 с. Новые Telegram-фото collector скачивает как JPEG с явно выбранным photo-size. Файлы более 64 MiB и изображения более 40 MP отклоняются. Florence далее использует свой закреплённый image processor. Генерация greedy (`num_beams=1`), без thinking и JSON-генерации. Native postprocessor извлекает текст подписи. JSON каталога формирует наш Python-код. Подпись ограничена 4000 символами; пустая сцена вызывает fallback на `<CAPTION>`, затем ошибку при повторном пустом ответе.

Английский scene кодируется тем же multilingual E5, что и русские запросы телефона; перевод не обязателен. Это совместимость векторного пространства, а не гарантия качества кросс-языкового поиска. Поиск абстрактного смысла мемов и точность OCR следует проверять на своём наборе; мелкие надписи могут потеряться при уменьшении. OCR не восстанавливает полный контекст чата.

## Настройки

```bash
# Быстрее, но без извлечения надписей:
indexer/.venv/bin/tg-describe data/archive/selected-catalog.json --device cuda --ocr none --output data/archive/captions-only.json

# Встроенный OCR Florence; качество кириллицы не подтверждено:
indexer/.venv/bin/tg-describe data/archive/selected-catalog.json --device cuda --ocr florence --output data/archive/florence-ocr.json

# Более длинная подпись (до 512 новых токенов):
indexer/.venv/bin/tg-describe data/archive/selected-catalog.json --device cuda --max-tokens 256
```

Смена профиля требует повторной обработки. Параметры Ollama `--timeout`, `--context-size` относятся только к `--backend ollama`; для Florence применяются `--max-tokens`, `--device`, `--ocr`, `--limit`, `--output`.

## Собрать базу и отправить на телефон

После успешного полного прогона:

```bash
indexer/.venv/bin/tg-index export data/archive/florence-catalog.json models/e5-small data/personal-florence-v1 --dataset-id personal --version 1
```

Затем в Bash с инструментами Rust:

```bash
source scripts/env.sh
cargo run --locked -p search-cli -- validate data/personal-florence-v1
indexer/.venv/bin/tg-index pack data/personal-florence-v1 data/personal-florence-v1.zip
```

Если архив personal уже имеет версию 1 или выше, выберите больший номер и новое имя папки. Перенесите ZIP в «Загрузки» телефона и импортируйте в приложении; повторная установка E5 или APK не требуется. Подробный цикл — в [мини-гайде](QUICK_START.md).

Для сложных мемов сохранён отдельный backend Qwen, который нужно подготовить самостоятельно:

```bash
indexer/.venv/bin/tg-describe data/archive/selected-catalog.json --backend ollama --model qwen3-vl:2b-instruct --max-tokens 2048 --timeout 600
```

Его результат — прежний `described-catalog.json`. Автоматическая выборочная доразметка и смешивание результатов двух backend пока не реализованы.
