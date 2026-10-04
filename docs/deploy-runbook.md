# راهنمای انتشار و اتصال tennisino.com

هر بلوک را به ترتیب اجرا کن. دستورهای «روی ویندوز» در PowerShell داخل
`D:\Projects\SEOAGENT`، و دستورهای «روی سرور» بعد از `ssh seo-deploy`.

## ۱ و ۲. فرستادن و نصب نسخهٔ جدید

روی ویندوز (Git Bash)، یا Claude برایت اجرا می‌کند:

    bash deploy/ship.sh upload

همهٔ کارهای بدون sudo را انجام می‌دهد و در آخر **یک دستور sudo** چاپ می‌کند
که باید خودت در ترمینال سرور (`ssh seo-deploy`) بزنی. بعدش:

    bash deploy/ship.sh check

`app-release.sh` اگر `/readyz` جواب ندهد خودش به نسخهٔ قبل برمی‌گردد.
اسکریپت‌های بعدی را از نسخهٔ نصب‌شده اجرا کن:

    cd /opt/seoagent/current/deploy

## ۳. ایمنی، یک بار (روی سرور)

    sudo bash backup-setup.sh
    sudo seoagent-restore-test
    sudo bash watchdog-setup.sh
    sudo bash secret-key.sh

بعد از `secret-key.sh` یک نسخه از `/etc/seoagent/env` را جایی امن و جدا از
backup ها نگه دار. بدون آن کلید، رمزهای ذخیره‌شدهٔ سایت‌ها قابل خواندن نیستند.

## ۴. اتصال tennisino.com (روی سرور)

> بعد از این نسخه، هر سایتی که از service account مشترک استفاده می‌کند باید
> مالکیتش ثابت شده باشد، وگرنه تحلیل رد می‌شود. pama.shop هم شامل این است:
> یا meta tag را روی صفحهٔ اولش بگذار (`GET /v1/sites/{id}/ownership`) یا
> بعد از راه افتادن ورود با گوگل، با آن وصلش کن.

اول ببین Search Console این property را با چه رشته‌ای می‌شناسد:

    sudo bash smoke.sh

در خروجی آخرش فهرست property ها هست. همان رشته را **دقیقاً** بده (با اسلش
آخر، یا به شکل `sc-domain:tennisino.com`):

    sudo bash connect-site.sh https://tennisino.com/ <property-دقیق> Tennisino

نام کاربری وردپرس و application password را خود اسکریپت می‌پرسد؛ رمز روی
صفحه دیده نمی‌شود و جایی چاپ یا ذخیرهٔ خام نمی‌شود. در پایان باید `ok` و
نسخهٔ افزونه را ببینی. `site_id` را یادداشت کن.

## ۵. اتصال آروان (روی سرور)

با یک ویرایشگر (`sudo nano /etc/seoagent/env`) این‌ها را اضافه کن:

    SEO_AGENT_LLM_MODE=arvan
    SEO_AGENT_ARVAN_ENDPOINT=<آدرس endpoint از پنل آروان>
    SEO_AGENT_ARVAN_MODEL=<نام مدل>
    SEO_AGENT_ARVAN_API_KEY_REF=ARVAN_AI_KEY
    ARVAN_AI_KEY=<کلید>

بعد:

    sudo bash check-llm.sh

هر دو مرحله باید `ok` بدهند. هنوز `SEO_AGENT_TITLE_WORKFLOW_ENABLED` را
روشن نکن؛ مسیر عنوان به منبع SERP هم نیاز دارد.
