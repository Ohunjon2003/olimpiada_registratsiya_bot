# Olimpiada tizimi — Telegram bot + web panel

O'quvchilar **Telegram bot** orqali ro'yxatdan o'tadi, tashkilotchilar va hakamlar **web panel** orqali
ishtirokchilarni boshqaradi, ball kiritadi, reyting tuzadi va natija/sertifikatlarni botga yuboradi.
Bot va web bitta jarayonda ishlaydi, baza — SQLite (bitta fayl).

---

## 1. Imkoniyatlar

### Bot (o'quvchi uchun)
- Til tanlash: **o'zbek lotin / kirill** (kirill matnlar avtomatik o'giriladi, xohlasangiz qo'lda yozasiz).
- **Kanallarga obuna majburiy**: Telegram kanallar haqiqatan tekshiriladi (`getChatMember`), Instagram — havola + «Obuna bo'ldim» tugmasi. Instagram username so'rash admin tomonidan yoqib-o'chiriladi.
- **Nizom**: matn va/yoki PDF; «✅ Tanishib chiqdim va tushundim» bosilmaguncha keyingi bosqich ochilmaydi.
- Savollar alohida-alohida, har biri **namuna bilan**: ism → familiya → telefon → maktab → manzil → sinf → fan(lar) → admin qo'shgan qo'shimcha savollar.
- **Xato javobda o'tkazmaydi** — ogohlantirib, shu savolni qayta so'raydi.
- Telefon: «📱 Raqamni yuborish» tugmasi **yoki** qo'lda (`+998901234567`, `90 123 45 67` ...). Faqat o'z raqami qabul qilinadi.
- **Bitta telefon raqam = bitta o'quvchi**, bitta Telegram akkaunt = bitta o'quvchi.
- Maktab, sinf, fan — faqat **tugmadan tanlanadi** (filtrlashda chalkashlik bo'lmaydi). Maktabni raqami/nomi bilan qidirish mumkin. Fan sinfga qarab ko'rinadi, maksimal fanlar sonini admin belgilaydi.
- «⬅️ Orqaga», «❌ Bekor qilish», oxirida **tasdiqlash oynasi** va istalgan maydonni **tahrirlash**.
- Saqlashdan oldin obuna **qayta tekshiriladi**.
- Oxirida admin yozgan **tabrik/reklama matni** va ishtirokchi **ID raqami**.
- Menyu: 👤 Ma'lumotlarim, 📊 Natijalarim (ball + o'rin), 🎓 Sertifikat (PDF), 🌐 Til, ✏️ Ma'lumotni o'zgartirish (muddat ichida).
- Server qayta ishga tushsa ham o'quvchi to'xtagan joyidan davom etadi (holat bazada saqlanadi).
- **Barcha matnlar, namunalar va tugmalar paneldan o'zgartiriladi** (💬 Bot matnlari), emoji qo'shish paneli bilan.
- **Stikerlar**: salomlashishda, ro'yxatdan o'tgach va natijalar e'lon qilinganda — Telegram stikeri yoki katta animatsion emoji.
- Bot kanalda admin bo'lmasa yoki kanal ID xato bo'lsa, o'quvchilar to'xtab qolmaydi — admin bosh sahifada ogohlantiriladi.

### Web panel
- **Rollar**: admin (hammasi) va hakam (faqat ball kiritish). Login/parolni admin beradi, hakamni fanga biriktirish mumkin.
- **Hakamlar ismni ko'rsinmi yoki faqat ID** — admin yoqib-o'chiradi (xolislik uchun).
- **Ball kiritish**: raqamni yozib Enter — saqlanadi va keyingi o'quvchiga o'tadi. Ball va foiz avtomatik. Bir vaqtda istalgancha hakam (sinovda 20 hakam parallel, 1500 o'quvchi — xatosiz).
- **3 xil baholash usuli** (har fan uchun alohida):
  - *Bir xil ball* — savollar soni × bitta savol bali;
  - *Har xil ball* — savol guruhlari (masalan, 1–20: 1 ball, 21–30: 3 ball), hakam har guruhdagi to'g'ri javoblarni kiritadi;
  - *Qo'lda* — hakam ballni o'zi yozadi.
- **Reyting**: fan bo'yicha **va blok bo'yicha** (bir nechta fan birlashtiriladi: ballar yig'indisi yoki foizlar o'rtachasi). Sinf kesimida yoki umumiy. Teng ball — teng o'rin (1, 2, 2, 4).
- **Turlar** (1-tur, final): eng yaxshilarni keyingi turga bir tugma bilan o'tkazish.
- Ishtirokchilar: qidiruv va filtrlar (maktab, sinf, fan, holat, til, sana), tahrirlash, chetlatish, botda shaxsiy xabar, **Excel eksport**.
- **Sertifikat/diplom dizayneri** (Canva'ga o'xshash): fon — rang, gradient yoki rasm (Canva'da tayyorlangan rasmni ham yuklash mumkin); matn, logo/muhr/imzo rasmlari, to'rtburchak, doira, chiziq; sichqoncha bilan surish, cho'zish, burish, yopishuvchi yo'riqnoma chiziqlari, qatlamlar, Ctrl+Z/Y, avtosaqlash; 15 ta shrift (lotin + kirill, o'zbek harflari bilan) va o'z shriftingizni yuklash; soya, kontur, harf oralig'i; A4/A5, 150–300 dpi. 8 ta tayyor dizayn (oltin, ko'k, yashil, minimal, 1/2/3-o'rin diplomlari, bo'sh). Ekrandagi ko'rinish PDF bilan **aynan bir xil** (bir xil shrift o'lchamlari va joylashtirish algoritmi).
- **O'rinlar va mukofotlar** (🏅): o'rin ballga qarab avtomatik (1, 2, 2, 4). Har fan/blok uchun nechta sovrinli o'rin va minimal foiz. Shablon «kimga»: sovrindorlarga / faqat N-o'ringa / ishtirokchilarga / hammaga. `{place}`, `{place_text}`, `{place_roman}`, `{award}` o'zgaruvchilari. «Kim nima oladi» sahifasi, chop etish uchun bitta PDF yoki ZIP (fonda tayyorlanadi). Botga **rasm + PDF**, sovrindorga «🏆 Tabriklaymiz! … 1-o'rin» izohi bilan.
- **Joylashtirish** (🪑): bino → xonalar (qator × parta × partadagi o'rin, singan o'rinlarni o'chirish) → smenalar (avtomatik yaratish: sana, kuniga vaqtlar). «Taqsimlash» — o'quvchilar smena → xona → o'rinlarga: **bir maktab xonalarga tekis tarqaladi, bir xonaga tushsa ham bir partada, yonma-yon va old-orqada o'tirmaydi** (1500 o'quvchi — 0,3 soniya). Smenalarga bo'lish usuli tanlanadi: aralash yoki **maktablar tartibida** (masalan, 1–20-maktablar 1-smenada, 21–40-maktablar 2-smenada; bitta maktab ikki smenaga bo'linmaydi). Xona sxemasi (maktablar rangda), o'quvchini bosib ko'chirish/almashtirish, keyin qo'shilganlarni bo'sh joyga qo'yish.
- **Ruxsatnoma** (🎫): sana, kelish/boshlanish vaqti, bino, manzil (xarita), xona, qator/parta/o'rin — dizaynerda o'zgartiriladi. Olimpiadadan **bir kun oldin belgilangan soatda avtomatik** botga yuboriladi (rasm + PDF), botda «🎫 Ruxsatnoma» tugmasi. Joy o'zgarsa — yangisi avtomatik ketadi. Xona eshigiga ro'yxat va o'tirish sxemasi (PDF), maktablar bo'yicha Excel, davomatda smena/xona filtri.
- **Titul va avtomatik tekshiruv** (📝): har o'quvchiga shaxsiy titul (QR + ID doirachalari), A4 ga 4/2/1 tadan (savollar soniga qarab: 40/100/200 gacha), javoblar A–D, variantlar (A/B…) ixtiyoriy — yonma-yon o'tirganlarga turli variant. Hakam **telefon kamerasi** bilan ketma-ket skaner qiladi: 🟢 «qabul qilindi», 🟡 «oldin kiritilgan», 🟠 «admin tekshiradi», 🟣 «aniq o'qilmadi». Ikki belgi — xato. Kalitni olimpiadadan keyin kiritsa ham bo'ladi — hammasi qayta hisoblanadi. Fan bo'yicha yoqib-o'chiriladi (qo'lda kiritish ishlayveradi). Hakam faqat o'z fanini skaner qiladi va o'quvchi ismini ko'rmaydi.
  - **Titul almashib qolmasligi** — 3 qavat: (1) titullar smena → xona → o'rin tartibida chop etiladi, titulda xona va o'rin yozilgan — nazoratchi oldindan partalarga qo'yadi; (2) o'quvchi titulga **o'z ID sini** belgilaydi, skaner QR dagi ID bilan solishtiradi — mos kelmasa natija yozilmaydi; (3) bunday ishlar «🔎 Tekshirish kerak» navbatiga tushadi, admin rasmini ko'rib to'g'ri o'quvchiga yozadi.
  - Barcha ishlar rasmi saqlanadi: o'quvchi sahifasida va «Ishlar» bo'limida (filtrlar bilan) — ✓/✗ belgilangan titul. Tanlanganlarga yoki filtr bo'yicha **tekshirilgan titulni botga yuborish**.
- **Bosh sahifada bosqichlar paneli**: ro'yxat → joylashtirish → ruxsatnoma → davomat → skaner → natijalar → sertifikatlar — qaysi ish qolgani bir qarashda.
- **Xabar yuborish**: filtr bo'yicha, rasm bilan, **rejalashtirilgan** (eslatmalar uchun), natijalarni va sertifikatlarni hammaga yuborish. Botni bloklaganlar belgilanadi.
- Kanallar, nizom, maktablar (ro'yxatni birdaniga qo'shish), sinflar, fanlar, bloklar, turlar, qo'shimcha savollar — hammasi paneldan.
- **Davomat** (🙋): olimpiada kuni kirishda «✅ Keldi / ❌ Kelmadi». Familiya/ism/ID ning birinchi harflarini yozish bilan ro'yxat darhol chiqadi (Enter shart emas; bitta o'quvchi qolsa Enter uni «Keldi» qiladi). Sinf tugmalari, maktab/fan/holat filtrlari, jonli hisoblagich. Ishtirokchilar ro'yxati, ball kiritish va ommaviy xabarda «kelganlar / kelmaganlar» filtri. Hakamlarga ruxsat — sozlamada.
- **E'tirozlar** (⚖️): natija e'lon qilingach o'quvchi botdan fan tanlab e'tiroz yozadi (har fan bo'yicha bitta, muddat bilan). Admin panelda qabul qiladi/rad etadi — javob o'quvchiga botda boradi.
- **Telegram xabarnomalar** (🔔): admin o'z Telegram'ini ulaydi; bot har N-o'quvchi, xatolar, yangi e'tirozlar haqida xabar beradi, **avtomatik zaxirani Telegram'ga yuboradi**. Botda `/stat` — tezkor statistika.
- **Arxiv va tozalash** (🗄): hamma narsa bitta Excel'da (xulosa, ishtirokchilar, har fan/blok reytingi, barcha natijalar, davomat, e'tirozlar). Yangi olimpiada uchun eski ma'lumotlarni tozalash (oldin avtomatik zaxira, so'z + parol bilan tasdiqlash).
- **Jurnal**: kim, qachon, nimani o'zgartirgani (eski va yangi qiymat).
- **Zaxira nusxa**: avtomatik (oraliq sozlanadi) va qo'lda, yuklab olish.
- Xavfsizlik: bcrypt parollar, CSRF himoya, login urinishlarini cheklash, parol almashsa eski sessiyalar bekor bo'ladi.

---

## 2. Loyiha tuzilmasi

```
olimpiada/
├── app/
│   ├── main.py              # kirish nuqtasi: FastAPI + bot + fon vazifalari
│   ├── config.py            # .env sozlamalari
│   ├── db.py, models.py     # SQLite (WAL) va jadvallar
│   ├── bootstrap.py         # migratsiya + boshlang'ich ma'lumotlar
│   ├── cli.py               # admin yaratish, parol tiklash, zaxira
│   ├── utils.py             # transliteratsiya, telefon/ism tekshiruvi, vaqt
│   ├── services/            # ball, reyting, eksport, sertifikat, xabar, zaxira, jurnal
│   ├── bot/                 # matnlar, tugmalar, holatlar, handlerlar
│   └── web/                 # marshrutlar, shablonlar, CSS/JS
├── alembic/                 # baza tuzilmasi versiyalari
├── deploy/                  # setup.sh, update.sh, systemd, nginx
├── data/                    # (avtomatik) baza, yuklangan fayllar, zaxiralar
├── requirements.txt
└── .env.example
```

---

## 3. Tayyorgarlik

### 3.1. Bot yaratish
1. Telegramda **@BotFather** → `/newbot` → nom va username bering → **tokenni** saqlab qo'ying.
2. Telegram kanal(lar)ingizga botni **administrator** qilib qo'shing (hech qanday huquq shart emas, faqat admin bo'lsin). Aks holda obunani tekshirib bo'lmaydi.

### 3.2. Domen (tavsiya)
Admin va hakam parollari internetda **HTTPS** orqali yurishi uchun domen oling (masalan, `olimpiada.uz`) va
DNS'da `A` yozuvini server IP manziliga yo'naltiring. Domensiz ham ishlaydi (faqat IP, HTTP).

> **Titul skaneri uchun domen (HTTPS) zarur**: telefon brauzeri jonli kamerani faqat HTTPS saytda beradi.
> Domensiz skaner «📸 Suratga olish» rejimida ishlaydi (har titulni alohida suratga olish).

**Ishlab turgan serverga domen qo'shish** (ma'lumotlar va `.env` saqlanadi):
1. Domen sotib oling, DNS boshqaruvida `A` yozuv: `@` → server IP (masalan `159.89.25.17`). 5–30 daqiqa kuting;
   tekshirish: `ping olimpiada.uz` server IP sini ko'rsatishi kerak.
2. Serverda: `cd ~/olimpiada_registratsiya_bot && git pull && sudo bash deploy/setup.sh olimpiada.uz`
3. Skript bepul SSL oladi, HTTP → HTTPS yo'naltiradi va `COOKIE_SECURE=true` qiladi. Panel: `https://olimpiada.uz`.

---

## 4. DigitalOcean serverga o'rnatish

1. **Droplet yarating**: Ubuntu 24.04, *Basic* reja, **1 GB RAM / 1 vCPU** yetadi (1500 o'quvchi uchun). Region: Frankfurt yoki Amsterdam. SSH kalit yoki parol bilan.
2. Kompyuteringizdan loyihani serverga yuklang:
   ```bash
   scp olimpiada.zip root@SERVER_IP:/root/
   ssh root@SERVER_IP
   apt-get update && apt-get install -y unzip
   unzip olimpiada.zip && cd olimpiada
   ```
3. O'rnatish skriptini ishga tushiring:
   ```bash
   sudo bash deploy/setup.sh olimpiada.uz     # domen bilan (HTTPS avtomatik)
   # yoki
   sudo bash deploy/setup.sh                  # faqat IP bilan
   ```
   Skript bot tokeni, admin login va parolini so'raydi, so'ng hammasini o'rnatadi: Python muhiti, systemd xizmati, Nginx, firewall, SSL.
4. Brauzerda `https://olimpiada.uz` (yoki `http://SERVER_IP`) ni oching va admin bilan kiring.
5. **Kirgandan keyin**: «Parolni almashtirish» bo'limida parolni yangilang va serverdagi `/opt/olimpiada/.env` faylidan `ADMIN_PASSWORD` qiymatini o'chirib qo'ying.

### Foydali buyruqlar
```bash
sudo systemctl status olimpiada        # holat
sudo journalctl -u olimpiada -f        # jonli loglar
sudo systemctl restart olimpiada       # qayta ishga tushirish
sudo nano /opt/olimpiada/.env          # sozlamalarni o'zgartirish (keyin restart)
cd /opt/olimpiada && sudo -u olimpiada ./venv/bin/python -m app.cli reset-password admin   # parolni tiklash
cd /opt/olimpiada && sudo -u olimpiada ./venv/bin/python -m app.cli create-admin ali        # yangi admin
```

### Yangilash
Yangi versiya fayllarini serverga yuklab, uning papkasida: `sudo bash deploy/update.sh`
(avval zaxira olinadi, ma'lumotlar saqlanadi, baza tuzilmasi avtomatik yangilanadi).

---

## 5. O'z kompyuterda sinab ko'rish

**Linux / macOS:**
```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # BOT_TOKEN, SECRET_KEY, ADMIN_PASSWORD ni to'ldiring
uvicorn app.main:app --port 8000
```

**Windows (PowerShell)** — `&&` va `source` ishlamaydi, buyruqlarni alohida yozing:
```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
notepad .env                # BOT_TOKEN, SECRET_KEY, ADMIN_PASSWORD
uvicorn app.main:app --port 8000
```
Brauzer: http://127.0.0.1:8000 . **Diqqat:** bitta bot tokeni bilan bir vaqtda faqat bitta joyda (yoki server, yoki kompyuter) ishga tushiring — aks holda Telegram «conflict» xatosini beradi.
Terminalda har bir kelgan xabar `Bot ← <id> (xabar: ...)` ko'rinishida yoziladi — bot xabar olayotganini shundan bilasiz.

---

## 6. Olimpiadani sozlash tartibi (paneldan)

1. **Umumiy sozlamalar** — olimpiada nomi, ro'yxatdan o'tish muddati, maksimal fanlar soni, Instagram username so'rash.
   **Bot matnlari** — salomlashish, tabrik/reklama, barcha savol va namunalar, tugmalar, stikerlar.
2. **Kanallar** — Telegram kanal: havola `https://t.me/kanal` va Kanal ID `@kanal` (yopiq kanal uchun `-100...`). «Botni tekshirish» tugmasi bot admin ekanini tasdiqlaydi. Instagram: faqat havola.
3. **Nizom** — matn va/yoki PDF.
4. **Maktablar** — «Ro'yxatni birdaniga qo'shish» bilan hammasini bir yo'la kiriting.
5. **Sinflar** — 5–11 tayyor, keragicha o'zgartiring.
6. **Fanlar** — baholash usuli, savollar soni/ballari, qaysi sinflar uchun.
7. **Bloklar** (kerak bo'lsa) — masalan «Aniq fanlar»: Matematika + Fizika + Informatika.
8. **Qo'shimcha savollar** (kerak bo'lsa) — matn, son, bitta/ko'p tanlov, telefon; majburiy yoki ixtiyoriy.
9. **Hakamlar** — login/parol yarating (🎲 tugmasi parol yaratadi), fanga biriktiring.
10. Botni o'zingiz sinab ko'ring: ro'yxatdan o'ting, keyin ishtirokchini paneldan o'chirib tashlang.

### Olimpiadadan oldin
- **🔔 Telegram xabarnomalar** → «Akkaunt ulash» → havolani bosing. Botda `/stat` ishlashini tekshiring.
- **UptimeRobot**ni ulang (shu sahifadagi yo'riqnoma) — server to'xtasa darhol xabar keladi.
- **🪑 Joylashtirish**: Xonalar (bino, so'ng «101-110» kabi birdaniga) → Smenalar («Avtomatik yaratish») →
  ro'yxat yopilgach **Taqsimot → Taqsimlash** → xonalarni ko'zdan kechiring → **Ruxsatnoma → Rejalashtirish**
  (har o'quvchiga olimpiadadan bir kun oldin, masalan 18:00 da). Ro'yxatdan o'tish paytida «ruxsatnoma keyin yuboriladi»
  xabarini yoqish ham shu yerda.
- **Chop etish**: har xona eshigiga ro'yxat + sxema (PDF), maktablar uchun Excel.

### Olimpiada kuni
- Umumiy sozlamalarda **zaxira oralig'ini 1 soat** qiling.
- Kirishda **🙋 Davomat** sahifasida «Keldi»ni belgilang (bir nechta kompyuter/telefondan bir vaqtda mumkin).
- Hakamlar «Ball kiritish»da **Davomat → Faqat kelganlar** filtrini tanlaydi.
- Eslatmani oldindan rejalashtiring: «Xabar yuborish» → vaqt belgilang.
- Hakamlar «Ball kiritish» bo'limida ishlaydi; bosh sahifada har fan bo'yicha jarayon ko'rinadi.
- Tekshiruv tugagach fanni **qulflang** (Fanlar → «Natijalar qulflangan»).
- «Reyting» → **«Natijalarni e'lon qilish»** → «Natijalarni botga yuborish».
- Davomatda **Smena** va **Xona** filtrini tanlang — har xona nazoratchisi o'z xonasini o'rin tartibida belgilaydi.
- Barcha ballar kiritilgach **🏅 O'rinlar va mukofotlar** → turni qulflang (o'rinlar yakuniy) va sovrinli o'rinlar sonini tekshiring.
- Sertifikatlar: dizaynni tayyorlang → shablonni faol qiling («kimga»: sovrindorlar / ishtirokchilar) → «📋 Kim nima oladi»da tekshiring → sertifikatlarni yoqing → «Sertifikatlarni yuborish».
- Kerak bo'lsa **⚖️ E'tirozlar**ni muddat bilan yoqing.

### Titul bilan tekshiruv (ixtiyoriy)
1. **📝 Titul va skaner → Fanlar va kalitlar**: fanda «Titul bilan avtomatik tekshirish»ni yoqing, variantlar sonini tanlang.
   Fanning baholash usuli «Bir xil ball» yoki «Har xil ball» bo'lishi kerak (savollar soni titulga yoziladi).
2. **Joylashtirishdan keyin** «Titullarni chop etish» → PDF (smena/xona/o'rin tartibida). **100% masshtabda** chop eting
   (printerda «Fit to page» o'chirilgan). Kesib, xona bo'yicha nazoratchilarga bering.
3. Olimpiadadan oldin 10–20 ta titulni to'ldirib, telefoningizda sinab ko'ring (yorug' joy, titul ramkaga to'liq sig'sin).
4. Olimpiada kuni: o'quvchi ismini kitobchaga yozadi, titulga faqat **ID sini** belgilaydi. Hakam faqat titulni oladi.
5. Hakamlar **📷 Skaner** sahifasini telefonda ochadi (o'z login/paroli bilan) va titullarni birma-bir kameraga tutadi.
6. Kalitni kiriting («Kalitni saqlash va hisoblash») — hamma skanerlangan ishlar ballanadi.
7. **🔎 Tekshirish kerak** navbatini nolga tushiring (ID mos kelmagan, bo'sh, aniq o'qilmagan ishlar).
8. «Ishlar» bo'limidan tekshirilgan titullarni o'quvchilarga botda yuborish mumkin.

### Olimpiadadan keyin
- **🗄 Arxiv va tozalash** → «To'liq Excel yuklab olish» (o'rinlar varag'i ham bor), sertifikatlar PDF/ZIP va baza zaxirasini kompyuteringizga saqlang.
- Yangi olimpiadadan oldin shu sahifada eski ma'lumotlarni tozalang.

---

## 7. Zaxiradan tiklash
```bash
sudo systemctl stop olimpiada
cd /opt/olimpiada/data
sudo cp backups/olimpiada_YYYYMMDD_HHMMSS_auto.db olimpiada.db
sudo rm -f olimpiada.db-wal olimpiada.db-shm
sudo chown olimpiada:olimpiada olimpiada.db
sudo systemctl start olimpiada
```
Muhim zaxiralarni paneldan («Zaxira nusxalar») kompyuteringizga ham yuklab oling.

---

## 8. Muammolar va yechimlar

| Muammo | Yechim |
|---|---|
| Obuna tekshiruvi har doim «obuna bo'lmagansiz» deydi | Bot kanalda **admin** emas yoki Kanal ID noto'g'ri. «Kanallar» → «Botni tekshirish». |
| Bot javob bermayapti | `journalctl -u olimpiada -n 100`. Token to'g'rimi? Boshqa joyda shu token bilan bot ishlamayaptimi? |
| «Sahifa eskirgan (CSRF)» | Sahifani yangilang (uzoq ochiq qolgan sahifa). |
| Login bloklandi | 10 daqiqa kuting yoki serverda `app.cli reset-password`. |
| Fanni o'chirib bo'lmayapti | Uni tanlagan o'quvchilar bor — o'chirish o'rniga **nofaol** qiling. |

---

## 9. Texnik eslatmalar
- **SQLite + WAL**: bir vaqtda ko'p o'qish va navbatli yozish; natijalar atomar `UPSERT` bilan saqlanadi. Kelajakda PostgreSQL'ga o'tish SQLAlchemy tufayli oson.
- Xizmat **bitta worker** bilan ishlashi shart (bot polling).
- Telegram cheklovi uchun ommaviy xabar sekundiga 20 ta (`BROADCAST_RATE`). 1500 kishiga ~1,5–2 daqiqa.
- Bitta o'quvchining tez-tez bosgan tugmalari navbat bilan, turli o'quvchilarniki parallel qayta ishlanadi.
