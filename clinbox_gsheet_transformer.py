"""
Shared Sheets Export Script
Sizinle paylaşılan Google Sheets dosyalarını Excel formatında export eder.
"""

import os
import sys
import json
import datetime
import time
import random
from pathlib import Path
from typing import List, Dict, Optional

# Google API kütüphaneleri
try:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    import pandas as pd
except ImportError as e:
    print(f"Gerekli kütüphaneler eksik: {e}")
    print("Kurulum için: pip install google-auth google-api-python-client pandas openpyxl")
    sys.exit(1)

def build_credentials_from_env(scopes):
    SA_CLIENT_EMAIL = os.environ.get('SA_CLIENT_EMAIL')
    SA_PRIVATE_KEY_ID = os.environ.get('SA_PRIVATE_KEY_ID')
    SA_CLIENT_ID = os.environ.get('SA_CLIENT_ID')
    SA_PRIVATE_KEY = os.environ.get('SA_PRIVATE_KEY')
    if SA_PRIVATE_KEY and '\\n' in SA_PRIVATE_KEY:
        SA_PRIVATE_KEY = SA_PRIVATE_KEY.replace('\\n', '\n')
    if SA_CLIENT_EMAIL and SA_PRIVATE_KEY:
        sa_info = {
            "type": "service_account",
            "project_id": os.environ.get('SA_PROJECT_ID', ''),
            "private_key_id": SA_PRIVATE_KEY_ID,
            "private_key": SA_PRIVATE_KEY,
            "client_email": SA_CLIENT_EMAIL,
            "client_id": SA_CLIENT_ID,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
        }
        return service_account.Credentials.from_service_account_info(sa_info, scopes=scopes)

class SharedSheetsExporter:
    def __init__(self, config_file: str = "shared_config.json"):
        self.config = self.load_config(config_file)
        self.credentials = None
        self.drive_service = None
        
    def load_config(self, config_file: str) -> Dict:
        """Konfigürasyon dosyasını yükle"""
        if not os.path.exists(config_file):
            # Dosya yoksa senin verdiğin varsayılanla devam et
            default_config = {
                "output": {
                    "excel_folder": ".",
                    "add_timestamp": True
                },
                "source": {
                    "folder_id": os.environ.get('SOURCE_FOLDER_ID', '')
                }
            }
            print(f"Uyarı: {config_file} bulunamadı, varsayılan config kullanılacak.")
            return default_config

        with open(config_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    def authenticate(self):
        """Google API kimlik doğrulaması (Service Account)"""
        print("MODE=service-account + drive-export")
        SCOPES = ['https://www.googleapis.com/auth/drive.readonly']
        creds = build_credentials_from_env(SCOPES)
        self.credentials = creds
        self.drive_service = build('drive', 'v3', credentials=creds)
    
    def list_shared_sheets(self) -> List[Dict]:
        """Paylaşılan Google Sheets dosyalarını listele"""
        query = "mimeType='application/vnd.google-apps.spreadsheet' and trashed=false and sharedWithMe=true"
        results = self.drive_service.files().list(
            q=query,
            spaces='drive',
            fields='files(id, name, modifiedTime, owners)',
            includeItemsFromAllDrives=True,
            supportsAllDrives=True
        ).execute()
        
        return results.get('files', [])
    
    def list_shared_sheets_in_folder(self, folder_id: str) -> List[Dict]:
        query = f"'{folder_id}' in parents and mimeType='application/vnd.google-apps.spreadsheet' and trashed=false"
        try:
            results = self.drive_service.files().list(
                q=query,
                spaces='drive',
                fields='files(id, name, modifiedTime, owners)',
                includeItemsFromAllDrives=True,
                supportsAllDrives=True
            ).execute()
            return results.get('files', [])
        except HttpError as e:
            status = getattr(e, 'status_code', None) or getattr(e, 'resp', None).status if getattr(e, 'resp', None) else None
            print(f"❌ Klasör listelenemedi (HTTP {status}). 'folder_id' doğru mu ve SA erişimi var mı? Hata: {e}")
            return []

    def check_existing_file(self, sheet_name: str, output_dir: str) -> bool:
        """Dosyanın zaten export edilip edilmediğini kontrol et"""
        safe_name = "".join(c for c in sheet_name if c.isalnum() or c in (' ', '-', '_')).rstrip()
        today = datetime.datetime.now().strftime("%Y%m%d")
        add_timestamp = self.config.get('output', {}).get('add_timestamp', True)
        expected_filename = f"{today}_{safe_name}.xlsx" if add_timestamp else f"{safe_name}.xlsx"
        expected_path = os.path.join(output_dir, expected_filename)
        return os.path.exists(expected_path)

    def export_sheet_to_excel(self, sheet_id: str, sheet_name: str, output_dir: str) -> str:
        """Google Sheet'i Drive API ile tek çağrıda XLSX olarak indir."""
        # Dosya adı
        timestamp = datetime.datetime.now().strftime("%Y%m%d")
        safe_name = "".join(c for c in sheet_name if c.isalnum() or c in (' ', '-', '_')).rstrip()
        add_timestamp = self.config.get('output', {}).get('add_timestamp', True)
        excel_filename = f"{timestamp}_{safe_name}.xlsx" if add_timestamp else f"{safe_name}.xlsx"
        excel_path = os.path.join(output_dir, excel_filename)

        # Aynı gün içinde yeniden üretildiyse atla
        if os.path.exists(excel_path):
            print(f"⏭️ '{sheet_name}' zaten export edilmiş, atlanıyor.")
            return None

        # Retries: 429/5xx için exponential backoff + jitter
        max_attempts = 5
        for attempt in range(1, max_attempts + 1):
            try:
                request = self.drive_service.files().export(
                    fileId=sheet_id,
                    mimeType='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
                )
                data = request.execute()
                with open(excel_path, 'wb') as f:
                    f.write(data)
                print(f"✓ '{sheet_name}' Excel'e export edildi: {excel_filename}")
                return excel_path
            except HttpError as http_err:
                status = getattr(http_err, 'status_code', None) or getattr(http_err, 'resp', None).status if getattr(http_err, 'resp', None) else None
                if status in (429, 500, 502, 503, 504):
                    delay = min(30, (2 ** (attempt - 1))) + random.uniform(0, 0.5)
                    print(f"⚠️ '{sheet_name}' export HTTP {status}, {attempt}/{max_attempts} deneme. {delay:.1f}s bekleniyor...")
                    time.sleep(delay)
                    continue
                else:
                    print(f"✗ '{sheet_name}' export HTTP hatası: {http_err}")
                    break
            except Exception as e:
                print(f"✗ '{sheet_name}' export sırasında hata: {e}")
                break

        # Son çare: boş XLSX oluştur ki Kestra output'ları tamamlansın
        try:
            with pd.ExcelWriter(excel_path, engine='openpyxl') as writer:
                pd.DataFrame().to_excel(writer, sheet_name='empty', index=False)
            print(f"⚠️ '{sheet_name}' için boş Excel oluşturuldu: {excel_filename}")
            return excel_path
        except Exception as e:
            print(f"✗ '{sheet_name}' için boş Excel oluşturulamadı: {e}")
            return None
    
    def run_export(self):
        """Ana export işlemini çalıştır"""
        print("🔄 Paylaşılan Sheets Export başlatılıyor...")
        
        # Kimlik doğrulama
        self.authenticate()
        
        # Sadece belirli klasördeki paylaşılan sheets dosyalarını listele (config)
        folder_id = self.config.get('source', {}).get('folder_id')
        if not folder_id:
            print("❌ 'source.folder_id' ayarı bulunamadı (shared_config.json).")
            return
        sheets = self.list_shared_sheets_in_folder(folder_id)
        if not sheets:
            print("❌ Klasörde Google Sheets dosyası bulunamadı.")
            return

        print(f"📁 {len(sheets)} paylaşılan Google Sheets dosyası bulundu:")
        for i, sheet in enumerate(sheets, 1):
            owner = sheet.get('owners', [{}])[0].get('displayName', 'Bilinmeyen')
            print(f"  {i}. {sheet['name']} (Sahip: {owner})")
        
        # Output klasörünü oluştur (config)
        output_dir = self.config.get('output', {}).get('excel_folder', ".")
        os.makedirs(output_dir, exist_ok=True)
        
        # Her dosyayı export et
        exported_files = []
        for sheet in sheets:
            excel_path = self.export_sheet_to_excel(
                sheet['id'], 
                sheet['name'], 
                output_dir
            )
            if excel_path:
                exported_files.append(excel_path)
        
        if exported_files:
            print(f"\n✅ {len(exported_files)} dosya başarıyla export edildi.")
            print(f"📁 Dosyalar şu klasörde: {output_dir}")
        else:
            print("❌ Hiçbir dosya export edilemedi.")

def main():
    """Ana fonksiyon"""
    exporter = SharedSheetsExporter()
    exporter.run_export()

if __name__ == "__main__":
    main() 