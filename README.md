# UChat → Chatwoot Relay

سيرفر بيستقبل ملفات CSV/XLSX فيها subscribers، وبيهاجر محادثاتهم من UChat لـ Chatwoot
كـ private notes — **بيشتغل 24/7 على Railway من غير ما جهازك يفضل شغال**.

## المعمار

```
[ انت ترفع CSV/XLSX ]
          │  POST /upload
          ▼
   web  (FastAPI)  ──►  Postgres (queue + progress)  ◄──  worker (loop)
          │                                                    │
   GET /status                                          migrate_user()
                                                       UChat ──► Chatwoot
```

- **Dedup بالـ phone** على مستوى Postgres → ارفع الـ 30 ملف كلهم، المكرر بيتشال لوحده.
- **Resume-safe**: الـ progress في Postgres، مش في ملف. أي redeploy على Railway مايأثرش.
- **worker آمن للتكرار**: `FOR UPDATE SKIP LOCKED` → تقدر تشغّل أكتر من replica.

## Deploy على Railway

1. ارفع المشروع على GitHub repo (private).
2. في Railway: **New Project → Deploy from GitHub repo**.
3. ضيف **Postgres** (Add → Database → PostgreSQL). Railway هيحقن `DATABASE_URL` تلقائيًا.
4. اعمل **سيرفسين** من نفس الـ repo:
   - **web** → Start Command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
   - **worker** → Start Command: `python -m app.worker`
5. في كل سيرفس، ضيف الـ env variables (من `.env.example`):
   `UCHAT_API_TOKEN`, `CHATWOOT_BASE_URL`, `CHATWOOT_API_TOKEN`, `ACCOUNT_ID`, `INBOX_ID`
   (وكمان `DATABASE_URL` لو مش متشاركة تلقائيًا — اربطها بالـ Postgres reference variable).

> ملاحظة: السيرفسين الاتنين لازم ياخدوا نفس الـ `DATABASE_URL` وبيانات UChat/Chatwoot.

## الاستخدام (من المتصفح — من غير cmd)

افتح لينك سيرفس الـ **web** في المتصفح:
```
https://YOUR-WEB-URL.up.railway.app/
```
هتلاقي صفحة رفع: **اسحب كل الملفات بالماوس مرة واحدة → دوس "ابدأ الرفع"**.
الصفحة بترفع كل ملف ورا التاني وبتوريك جدول بالنتايج (جديد / مكرر / اتفلتر)،
وفيه قسم "حالة الترحيل" بيتحدّث lحظيًا.

### شيتات توزيع الموظفين (اختياري)

لو عندك شيتات فيها أرقام العملاء واسم الموظف المسؤول، ارفعها من كارت **توزيع الأرقام على الموظفين**
قبل رفع ملفات UChat. الشكل المتوقع:

| Contact Name | Phone | Salesperson |
|---|---|---|
| Example Customer | 966500000000 | Nader Aziz |

- لو رقم العميل موجود في شيت توزيع → المحادثة الجديدة في Chatwoot تتعمل assign للموظف.
- لو الرقم مش موجود في شيت توزيع → المحادثة بتتساب **unassigned** حتى العميل يبعت رسالة.
- لو أسماء `Salesperson` مش مطابقة لأسماء Agents في Chatwoot، حط mapping في env:

```bash
CHATWOOT_AGENT_MAP={"Ahmed  El-Shiekh":12,"Nader Aziz":13}
```

بديلًا عن ذلك، ممكن تضيف عمود `chatwoot_agent_id` أو `agent_id` في الشيت، وساعتها الكود يستخدم الـ ID مباشرة.

### بديل (اختياري) للـ CLI:
```bash
curl -F "file=@assignments.xlsx" https://YOUR-WEB-URL.up.railway.app/upload-assignments
curl -F "file=@users.csv" https://YOUR-WEB-URL.up.railway.app/upload
curl https://YOUR-WEB-URL.up.railway.app/status
```

رد الـ `/upload`:
```json
{ "job_id": 1, "rows_in_file": 980, "queued_new": 950,
  "duplicates_ignored": 30, "filtered_out_by_date": 0 }
```

> فحص صحة السيرفس: `GET /health`

## فلترة بالتاريخ (اختياري)

عايز تنقل اللي اتكلموا مؤخرًا بس؟ حط في الـ env بتاع **web**:
```
MIGRATE_SINCE=2025-11-01
```
أي contact آخر تفاعل ليه قبل التاريخ ده بياخد status=`skipped` ومابيتنقلش.

## التحكم في السرعة (من env، من غير ما تلمس الكود)

| Variable | Default | الوظيفة |
|---|---|---|
| `RATE_MSG_DELAY` | 0.5 | تأخير بين كل رسالة |
| `RATE_USER_DELAY` | 2 | تأخير بين كل عميل |
| `DOWN_BACKOFF` | 30 | لو Chatwoot وقع، يستنى ويرجّع العميل للـ queue |
| `BREAK_EVERY` / `BREAK_SECONDS` | 500 / 60 | استراحة دورية لحماية الـ APIs |
| `CHATWOOT_AGENT_MAP` | empty | mapping اختياري من اسم الموظف في Excel إلى Chatwoot agent ID |

## ملاحظة أمان

التوكنات **env-only** — مفيش أي توكن hardcoded في الكود. لو كنت كاشف توكنات قديمة
في الريبو القديم، اعملهم rotate (واتعملت already حسب كلامك).
