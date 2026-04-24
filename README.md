# WhatsApp AI Bot with Green API

Жоба қазір 2 Google Sheet-пен жұмыс істейді:

- `Products` - тауар каталогы
- `Clients` - статусымен бірге финал клиенттер базасы

## Қысқа схема

1. Клиент жазады
2. Бот тауарды көрсетеді
3. Заказ кезінде түс, саны, аты-жөні, телефоны, адресі жиналады
4. Төлем таңдауы:
   - `Kaspi QR`
   - `Удаленка`
5. `Kaspi QR` болса:
   - QR фото жіберіледі
   - клиент чек жібереді
   - `Clients` sheet-ке `paid` статусымен жазылады
6. `Удаленка` болса:
   - клиенттен Kaspi номері сұралады
   - бот `Төледім` / `Бас тарту` батырмаларын жібереді
   - қайсысын басса, сол статуспен `Clients` sheet-ке жазылады

## Google Sheet

### 1. `Products`

Бірінші қатарға осындай header-лер ыңғайлы:

```text
Product Name | Regular Price | Sale Price | Kaspi Installment | Available Colors | Dimensions (Size) | Main Description | Delivery Fee | Delivery Time | Photo URL 1 | Photo URL 2 | Video URL | Link
```

Ұсынылатын негізгі product row:

```text
Product Name: LUNA — дизайнерский столик с зеркалом
Regular Price: 150 000 тг
Sale Price: 100 000 тг
Kaspi Installment: есть
Available Colors: Белый, Серый, Черный, Красный, Коричневый, Розовый, Бирюзовый
Main Description: LUNA — дизайнерский столик с зеркалом. Мягкий bouclé, округлые формы, встроенное зеркало, мягкое сиденье и удобное хранение. Акция: вместо 150 000 тг — 100 000 тг. LUNA — уютная beauty-зона и премиальный акцент для интерьера.
Delivery Fee: 10 000 тг
Delivery Time: 2-5 дней
Photo URL 1: main.jpeg
Video URL: main2.MOV
```

Егер әр түске жеке фото жібергіңіз келсе, ең ыңғайлысы файлдарды папкаға бөлу:

```text
assets/products/colors/white/
assets/products/colors/black/
assets/products/colors/beige/
assets/products/colors/pink/
assets/products/colors/gray/
```

Мысалы:

```text
assets/products/colors/white/white1.jpg
assets/products/colors/white/white2.jpg
assets/products/colors/black/black1.jpg
assets/products/colors/beige/beige1.jpg
```

Немесе бір папкада prefix-пен сақтауға болады:

```text
assets/products/white_1.jpg
assets/products/white_2.jpg
assets/products/black_1.jpg
```

Клиент `ақ түсті көрсет` не `черный цвет покажите` десе, AI сол түсті шешеді, ал код дәл сол түске сәйкес local файлдарды жібереді.

Қаласаңыз, қосымша ретінде `White Photos | Black Photos ...` сияқты sheet бағандарын да қолдануға болады, бірақ бұл міндетті емес.

### 2. `Clients`

Бұл парақты бос қалдыра беруге болады. Header-ді Apps Script өзі қояды.

## Apps Script

`Extensions > Apps Script` ашып, [apps_script/catalog_web_app.gs](C:\Users\user\Documents\whatsap AI agent\apps_script\catalog_web_app.gs) кодын қойыңыз.

Сосын:

1. `BOT_SHARED_SECRET` мәнін өзіңізге ауыстырыңыз
2. `.env` ішіндегі `GOOGLE_APPS_SCRIPT_SECRET` сонымен бірдей болсын
3. `Deploy > Manage deployments > Edit > Deploy`

## .env

Қажетті негізгі мәндер:

```env
GREEN_API_ID_INSTANCE=...
GREEN_API_TOKEN_INSTANCE=...
OPENAI_API_KEY=...
GOOGLE_APPS_SCRIPT_URL=...
GOOGLE_APPS_SCRIPT_SECRET=...
PAYMENT_KASPI_QR_FILE=assets/payment/kaspi_qr.jpg
CLIENTS_SHEET_NAME=Clients
```

## Іске қосу

```powershell
python -m pip install -r requirements.txt
python launch_bot.py
```

Тоқтату:

```powershell
python stop_bot.py
```

## Railway-ге шығару

Бұл жоба Railway-дің long-running service форматына ыңғайлы. Қазіргі polling, memory және reminder логикасы осы нұсқада сақталады.

### 1. Railway env vars

Service Variables ішіне мыналарды қосыңыз:

```text
GREEN_API_ID_INSTANCE
GREEN_API_TOKEN_INSTANCE
OPENAI_API_KEY
GOOGLE_APPS_SCRIPT_URL
GOOGLE_APPS_SCRIPT_SECRET
PAYMENT_KASPI_QR_FILE=assets/payment/kaspiqr.jpeg
CLIENTS_SHEET_NAME=Clients
```

### 2. Start command

Жобада [railway.toml](C:\Users\user\Documents\whatsap AI agent\railway.toml) дайын тұр. Start command:

```text
python -u main.py
```

### 3. Ең маңызды қадам: Volume

Чат жады мен reminder күйі restart-тан кейін сақталуы үшін Railway Volume жалғау керек.

Railway docs бойынша volume-ды relative `./data` үшін `/app/data` path-ке mount еткен дұрыс:
[Railway Volumes](https://docs.railway.com/guides/volumes)

Яғни:

```text
Mount path: /app/data
```

Сонда бот мына файлды persistent түрде қолданады:

```text
/app/data/chat_state.json
/app/data/ai_history.json
```

### 4. Deploy реті

1. GitHub-қа push
2. Railway-ге repo import
3. Variables толтыру
4. Volume attach ету: `/app/data`
5. Deploy
6. Green API instance қосулы екенін тексеру

### 5. Нәтиже

Осы нұсқада:

- polling қалады
- reminder қалады
- чат stage/state файлға сақталады
- restart болса да volume арқылы контекст жоғалмайды
