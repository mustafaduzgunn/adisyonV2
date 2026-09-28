# Adisyon — Restoran Masa Sipariş Uygulaması

Basit, tek adres üzerinde çalışan, mobil uyumlu masa siparişi uygulaması.
Flask + SQLite ile yazıldı, ek bir veritabanı sunucusuna ihtiyaç duymaz.

## Kurulum

Python 3.10+ gerekir. Menü PDF'i (Menu Designer) için **WeasyPrint** kullanılır;
bu paket pip'in yanında bazı sistem kütüphaneleri de ister (aşağıda ortama göre).

### 1) Sistem paketleri (PDF motoru)

**Ubuntu / Debian**
```bash
sudo apt update
sudo apt install -y python3-venv python3-pip libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 fonts-dejavu-core
```

**Docker** (`python:3.12-slim` tabanlı örnek Dockerfile)
```dockerfile
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn
COPY . .
EXPOSE 5000
# adisyon.db kalıcı olsun diye /app dizinine bir volume bağlayın
CMD ["sh", "-c", "python -c 'import app; app.init_db()' && gunicorn -w 4 -b 0.0.0.0:5000 app:app"]
```

**macOS**
```bash
brew install pango
```

**Windows**
1. MSYS2 kurun: `winget install MSYS2.MSYS2`
2. MSYS2 terminalinde: `pacman -S mingw-w64-x86_64-pango`
3. Python'un pango'yu bulması için ortam değişkenini tanımlayın (PowerShell):
   `setx WEASYPRINT_DLL_DIRECTORIES "C:\msys64\mingw64\bin"` ve terminali yeniden açın.

Ayrıntılar: https://doc.courtbouillon.org/weasyprint/stable/first_steps.html

### 2) Python paketleri ve çalıştırma

```bash
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Uygulama `http://<sunucu-ip>:5000` adresinde çalışır. Aynı ağdaki mobil
cihazlardan bu adrese tarayıcıyla girilerek kullanılabilir (garsonlar
telefonlarına kısayol/ana ekrana ekle yapabilir).

PDF motoru kurulu değilse uygulamanın geri kalanı normal çalışır; sadece
Menu Designer'daki **Download PDF** butonu hata mesajı verir.

İlk çalıştırmada `adisyon.db` dosyası otomatik oluşturulur ve örnek
veriler eklenir:

- Yönetici hesabı: **admin / admin123** (ilk girişten sonra mutlaka
  değiştirin — şu an için şifre değiştirme ekranı yok, en hızlı yol
  Garsonlar sayfasından yeni bir yönetici hesabı açıp eskisini silmek,
  ya da doğrudan veritabanından güncellemek).
- 10 örnek masa (Masa 1..10)
- 2 örnek kategori ve birkaç örnek ürün

## Menu Designer (menü tasarımı, PDF, yayınlama, QR)

Yönetici hesabıyla **Menü → Menu Designer** sayfasından:

- **Template seçimi:** 6 hazır tasarım (Classic Cream, Midnight Gold, Rustic Kraft,
  Fresh Green, Modern Mono, Burgundy). Kendi arka plan görselinizi
  (JPG/PNG/WebP, en fazla 8 MB) yükleyebilir veya düz renk seçebilirsiniz.
- **Canva benzeri düzenleme:** Önizlemedeki menü başlığına, alt başlığa,
  kategori adlarına, ürün adlarına ve fiyatlara tıklayıp doğrudan yazın
  (kategori/ürün/fiyat değişiklikleri veritabanına anında işlenir).
  Kategorileri ↑ ↓ ile sıralayın. Yan panelden renk, yazı tipi, boyut,
  hizalama, sütun sayısı ve para birimi simgesini değiştirin. Tasarım
  değişiklikleri otomatik kaydedilir (taslak).
- **PDF:** *Download PDF* taslağın A4 PDF çıktısını indirir.
- **Publish:** Taslağın o anki halinin (tasarım + fiyatlar) kopyasını
  `http://<program-adresi>/menu` adresinde **giriş yapmadan** herkese açar.
  Yayınlandıktan sonra yapılan değişiklikler tekrar *Publish* yapılana kadar
  canlı menüye yansımaz. *Unpublish* menüyü kapatır.
- **QR kod:** Programın adresini girin (`restoran.com` veya
  `http://100.100.100.100:5000`), *Generate* ile `<adres>/menu` için QR
  üretilir, *Download PNG* ile indirilir. Şema yazılmazsa alan adları
  `https://`, IP/port/localhost adresleri `http://` kabul edilir.

Menüde yalnızca **aktif** ürünler ve içinde aktif ürün bulunan kategoriler görünür.
Ürün/kategori ekleme-silme ve aktif/pasif işlemleri mevcut **Menü** sayfasından yapılır.

## Roller

- **Yönetici**: menü kategorileri/ürünleri ve fiyatları yönetir, masa
  sayısını değiştirir, garson/yönetici hesabı oluşturur veya siler,
  masa hesabını kapatır.
- **Garson**: sadece masalara girip sipariş ekleyebilir/kaldırabilir.
  Menü, masa ayarları ve kullanıcı yönetimine erişemez.

## Eş zamanlı kullanım

Bir masanın sipariş ekranı 3 saniyede bir kendini otomatik yeniler, bu
sayede aynı masaya birden fazla garson (veya garson + yönetici) aynı
anda girip sipariş ekleyebilir; her ekleme ayrı bir satır olarak
kaydedildiği için çakışma olmaz. Masa listesi ekranı da 4 saniyede bir
kendini yeniler.

## Üretim ortamı notu

`python app.py` geliştirme sunucusudur. Gerçek kullanımda (restoran
ortamında sürekli açık kalması için) `gunicorn` gibi bir WSGI sunucusu
ile çalıştırmanız önerilir, örnek:

```bash
pip install gunicorn
gunicorn -w 4 -b 0.0.0.0:5000 app:app
```

`-w 4` ile 4 worker process; masa/sipariş verisi SQLite + WAL modunda
tutulduğu için worker'lar arasında sorun yaşanmaz.

## Dosya yapısı

```
app.py                     -> tüm route'lar ve veritabanı işlemleri
menu_designer.py           -> Menu Designer modülü (blueprint: tasarım, PDF, publish, QR)
requirements.txt
templates/                 -> Jinja2 şablonları
  menu_designer.html       -> tasarım editörü
  menu_sheet.html          -> menü sayfası (editör, /menu ve PDF ortak kullanır)
  menu_public.html         -> herkese açık /menu sayfası
  menu_pdf.html            -> PDF şablonu
static/style.css           -> uygulama stilleri
static/menu.css            -> menü sayfası stilleri (templateler burada)
static/menu_designer.css/js-> editör arayüzü
adisyon.db                 -> otomatik oluşturulan SQLite veritabanı
```
