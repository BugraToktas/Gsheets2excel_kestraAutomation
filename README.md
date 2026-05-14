# 📊 Clinbox GSheets → Excel Pipeline

> **Google Sheets'teki paylaşılan dosyaları otomatik olarak Excel'e dönüştürür ve Cloudflare R2'ye yükler.**  
> Kestra workflow orchestration engine üzerinde günlük çalışacak şekilde zamanlanmış, Docker tabanlı, tamamen otomasyonlu bir veri pipeline'ıdır.

---

## 📁 Proje Dosyaları

| Dosya | Açıklama |
|---|---|
| `clinbox_gsheet_transformer.py` | Google Sheets → `.xlsx` dönüşümünü gerçekleştiren Python scripti |
| `clinbox_pdb_excel_uploader.yml` | Kestra workflow tanımı (zamanlama, Docker, S3/R2 upload) |

---

## 🏗️ Mimari & Akış

```
┌─────────────────────────────────────────────────────┐
│                  Kestra (Scheduler)                 │
│           Her gece @daily trigger ile çalışır       │
└──────────────────────┬──────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────┐
│           Docker Container (python:slim)            │
│                                                     │
│  1. pip install dependencies                        │
│  2. python clinbox_gsheet_transformer.py            │
│                                                     │
│  ┌───────────────────────────────────────────────┐  │
│  │         SharedSheetsExporter (Python)         │  │
│  │                                               │  │
│  │  authenticate()  ← Service Account (env vars) │  │
│  │       ↓                                       │  │
│  │  list_shared_sheets_in_folder(folder_id)      │  │
│  │       ↓                                       │  │
│  │  export_sheet_to_excel()  ← Drive Export API  │  │
│  │       ↓                                       │  │
│  │  14 x .xlsx dosyası oluşturulur               │  │
│  └───────────────────────────────────────────────┘  │
└──────────────────────┬──────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────┐
│         14 Paralel S3 Upload Görevi                 │
│   (Cloudflare R2 → pdb-excel-backup bucket)         │
└─────────────────────────────────────────────────────┘
```

---

## 🐍 Python Script: `clinbox_gsheet_transformer.py`

### Genel Bakış

`SharedSheetsExporter` sınıfı üç temel adımı yönetir:
1. **Kimlik Doğrulama** → Google Service Account ile Drive API'ye bağlanır
2. **Dosya Listeleme** → Belirtilen Drive klasöründeki tüm Sheets'leri bulur
3. **Export & Kaydetme** → Her Sheet'i `.xlsx` olarak indirir

### Sınıf & Metodlar

#### `build_credentials_from_env(scopes)`
Ortam değişkenlerinden Google Service Account kimlik bilgilerini derler.

| Ortam Değişkeni | Açıklama |
|---|---|
| `SA_CLIENT_EMAIL` | Service Account e-posta adresi |
| `SA_PRIVATE_KEY` | RSA özel anahtarı (`\n` karakterleri handle edilir) |
| `SA_PRIVATE_KEY_ID` | Anahtar ID |
| `SA_CLIENT_ID` | Client ID |

> ⚠️ `SA_PRIVATE_KEY` içindeki `\n` kaçış dizileri otomatik olarak gerçek satır sonu karakterlerine dönüştürülür.

---

#### `SharedSheetsExporter.__init__(config_file)`
`shared_config.json` dosyasından yapılandırmayı yükler. Dosya yoksa varsayılan değerler kullanılır:

```json
{
  "output": {
    "excel_folder": ".",
    "add_timestamp": true
  },
  "source": {
    "folder_id": "YOUR_GOOGLE_DRIVE_FOLDER_ID"
  }
}
```

---

#### `authenticate()`
- Scope: `https://www.googleapis.com/auth/drive.readonly` (salt okunur)
- Google Drive API v3 istemcisi oluşturur

---

#### `list_shared_sheets_in_folder(folder_id)`
Drive API'ye MIME type filtresi ile sorgu atar:
```
mimeType='application/vnd.google-apps.spreadsheet' AND trashed=false AND '{folder_id}' in parents
```
- `includeItemsFromAllDrives=True` → Shared Drive desteği
- HTTP hatalarında detaylı hata mesajı basar ve boş liste döner

---

#### `export_sheet_to_excel(sheet_id, sheet_name, output_dir)`

| Özellik | Detay |
|---|---|
| **Dosya adı formatı** | `YYYYMMDD_SafeName.xlsx` (timestamp isteğe bağlı) |
| **Idempotency** | Aynı gün içinde tekrar çalışırsa mevcut dosyayı atlar (`⏭️`) |
| **Export yöntemi** | `drive.files().export()` → direkt XLSX indirir, Sheets API'ye gerek yok |
| **Retry mantığı** | HTTP 429/500/502/503/504 için **exponential backoff + jitter** (max 5 deneme, max 30s bekleme) |
| **Fallback** | Tüm denemeler başarısız olursa Kestra output'larının bozulmaması için **boş XLSX** oluşturur |

```
Deneme 1: bekleme ~1s
Deneme 2: bekleme ~2s
Deneme 3: bekleme ~4s
Deneme 4: bekleme ~8s
Deneme 5: bekleme ~16s (+0-0.5s jitter)
```

---

#### `run_export()`
Pipeline'ın ana orkestrasyon metodu:
1. `authenticate()` çağrısı
2. `config.source.folder_id`'den klasör ID alır
3. Klasördeki tüm Sheet'leri listeler
4. `output.excel_folder`'ı oluşturur (yoksa)
5. Her Sheet için `export_sheet_to_excel()` çağırır
6. Özet istatistikleri basar

---

## ⚙️ Kestra Workflow: `clinbox_pdb_excel_uploader.yml`

### Genel Bilgiler

| Alan | Değer |
|---|---|
| **Workflow ID** | `clinbox_pdb_excel_uploader` |
| **Namespace** | `collabry` |
| **Zamanlama** | Her gece `@daily` (kayıp çalışmalar kurtarılmaz: `recoverMissedSchedules: NONE`) |
| **Çalışma ortamı** | Docker `python:slim` container |

### Üretilen Dosyalar (14 adet)

| Değişken | Dosya Adı Formatı |
|---|---|
| `file_name_api` | `YYYYMMDD_pdb_api-cllb.xlsx` |
| `file_name_crm` | `YYYYMMDD_pdb_crm-cllb.xlsx` |
| `file_name_datalog` | `YYYYMMDD_pdb_datalog-cllb.xlsx` |
| `file_name_decision` | `YYYYMMDD_pdb_decision-cllb.xlsx` |
| `file_name_marketing` | `YYYYMMDD_pdb_marketing-cllb.xlsx` |
| `file_name_metric` | `YYYYMMDD_pdb_metric-cllb.xlsx` |
| `file_name_organization` | `YYYYMMDD_pdb_organization-cllb.xlsx` |
| `file_name_process` | `YYYYMMDD_pdb_process-cllb.xlsx` |
| `file_name_quality` | `YYYYMMDD_pdb_quality-cllb.xlsx` |
| `file_name_training` | `YYYYMMDD_pdb_training-cllb.xlsx` |
| `file_name_checklist` | `YYYYMMDD_pdb-checklist-cllb.xlsx` |
| `file_name_content` | `YYYYMMDD_pdb-content-cllb.xlsx` |
| `file_name_resource` | `YYYYMMDD_pdb-resource-cllb.xlsx` |
| `file_name_rjb_wprd` | `YYYYMMDD_pdb-rjb-wprd.xlsx` |

### Task Akışı

```
transform_gsheets (Shell/Docker)
        │
        ├── upload_api         ─┐
        ├── upload_crm          │
        ├── upload_datalog      │
        ├── upload_decision     │
        ├── upload_marketing    │  14 adet S3 Upload görevi
        ├── upload_metric       │  (Cloudflare R2'ye yükler)
        ├── upload_organization │
        ├── upload_process      │
        ├── upload_quality      │
        ├── upload_training     │
        ├── upload_checklist    │
        ├── upload_content      │
        ├── upload_resource     │
        └── upload_rjb_wprd    ─┘
```

### Cloudflare R2 Yapılandırması

`pluginDefaults` ile tüm upload task'ları aynı R2 ayarlarını kullanır:

| Ayar | Değer |
|---|---|
| **Bucket** | `pdb-excel-backup` |
| **Content-Type** | `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` |
| **Endpoint** | `{{ secret('CLOUDFLARE_R2_ENDPOINT') }}` |
| **Region** | `auto` |
| **Auth** | Access Key + Secret Key (Kestra Secret Store) |

---

## 🔐 Gerekli Kestra Secrets

Aşağıdaki secret'ların Kestra'da tanımlı olması gerekir:

| Secret Adı | Açıklama |
|---|---|
| `GOOGLE_SERVICE_ACCOUNT_PDB_PRIVATE_KEY` | Google SA RSA özel anahtarı |
| `GOOGLE_SERVICE_ACCOUNT_PDB_CLIENT_EMAIL` | Google SA e-posta adresi |
| `GOOGLE_SERVICE_ACCOUNT_PDB_PRIVATE_KEY_ID` | Google SA anahtar ID |
| `GOOGLE_SERVICE_ACCOUNT_PDB_CLIENT_ID` | Google SA client ID |
| `CLOUDFLARE_R2_ENDPOINT` | R2 bucket endpoint URL |
| `CLOUDFLARE_R2_ACCESS_KEY` | R2 erişim anahtarı |
| `CLOUDFLARE_R2_SECRET_KEY` | R2 gizli anahtarı |

---

## 🛠️ Yerel Geliştirme & Test

### Bağımlılıkların Kurulması

```bash
pip install google-auth google-api-python-client pandas openpyxl
```

### Ortam Değişkenlerinin Ayarlanması

```bash
# Linux/macOS
export SA_CLIENT_EMAIL="your-sa@project.iam.gserviceaccount.com"
export SA_PRIVATE_KEY="-----BEGIN RSA PRIVATE KEY-----\n...\n-----END RSA PRIVATE KEY-----"
export SA_PRIVATE_KEY_ID="abc123"
export SA_CLIENT_ID="123456789"

# Windows (PowerShell)
$env:SA_CLIENT_EMAIL = "your-sa@project.iam.gserviceaccount.com"
$env:SA_PRIVATE_KEY  = "-----BEGIN RSA PRIVATE KEY-----`n...`n-----END RSA PRIVATE KEY-----"
```

### Scripti Çalıştırma

```bash
python clinbox_gsheet_transformer.py
```

### İsteğe Bağlı: `shared_config.json`

```json
{
  "output": {
    "excel_folder": "./output",
    "add_timestamp": true
  },
  "source": {
    "folder_id": "YOUR_GOOGLE_DRIVE_FOLDER_ID"
  }
}
```

> Dosya yoksa script varsayılan değerleri kullanır ve uyarı verir.

---

## 🔧 Google Drive Kurulumu

1. [Google Cloud Console](https://console.cloud.google.com/)'da bir **Service Account** oluşturun.
2. **Drive API** ve (gerekirse) **Sheets API**'yi etkinleştirin.
3. Service Account'a hedef Drive klasörünü **"Viewer" olarak paylaşın**.
4. Service Account JSON anahtarını indirin → bilgileri `SA_*` env değişkenlerine aktarın.

---

## 🚨 Hata Yönetimi

| Senaryo | Davranış |
|---|---|
| Eksik Python bağımlılıkları | `sys.exit(1)` ile çıkış, kurulum komutu gösterir |
| `shared_config.json` bulunamadı | Varsayılan config ile devam eder, uyarı basar |
| Drive klasörü erişim hatası | `❌` mesajı basar, boş liste döner |
| Export HTTP 429/5xx | 5 denemeye kadar exponential backoff + jitter |
| Tüm denemeler başarısız | Boş XLSX oluşturur (Kestra output hatası önlenir) |
| Aynı gün tekrar çalışma | Mevcut dosyayı atlar (`⏭️`) |

---

## 📦 Teknoloji Yığını

| Katman | Teknoloji |
|---|---|
| **Orkestrasyon** | [Kestra](https://kestra.io/) |
| **Çalışma Ortamı** | Docker (`python:slim`) |
| **Kimlik Doğrulama** | Google Service Account (OAuth2) |
| **Drive API** | `google-api-python-client` v3 |
| **Excel İşleme** | `pandas` + `openpyxl` |
| **Depolama** | Cloudflare R2 (S3-uyumlu) |

---

## 🗂️ Proje Yapısı

```
Gsheets2excel/
├── clinbox_gsheet_transformer.py   # Ana Python dönüşüm scripti
├── clinbox_pdb_excel_uploader.yml  # Kestra workflow tanımı
├── shared_config.json              # (İsteğe bağlı) Yapılandırma dosyası
└── README.md                       # Bu dosya
```

---